# tests/ — 단위 + E2E

루트에서 실행: `python3 -m pytest tests/unit -q -n auto` — CI 와 `automation/local_ci.sh` 가 도는 명령 그대로다(pytest-xdist, `requirements-dev.txt` 고정판). `-n auto` 없이 직렬로 돌려도 같은 결과여야 한다(루트 pytest 설정 없음 — 기본값 + `.pytest_cache`).
`configs/rag/*` 서비스는 자체 `pyproject.toml`의 pytest를 별도로 가진다(`tests/**`는 S101 제외).

## 구조
```
tests/
├── unit/                # 순수 로직·게이트·상태머신 단위 테스트 (56+)
└── e2e/
    ├── drivers/         # 게이트·피어·훅 경계를 구동하는 드라이버
    ├── scenarios/       # 종단 시나리오 스크립트
    └── fixtures/        # 고정 입력 (예: w2-personal-memory)
```

## e2e 규약
- scenario YAML(`scenarios/*.yaml`)은 `cases`/`expect` 스키마. driver/actor가 `OBS-JSON` 한 줄을 출력하고 `drivers/judge_expectations.py`가 exact-equality로 판정.
- fixture는 `e2e/fixtures/<시나리오>/`에 시나리오 전용 자산(입력 md + 기록된 LLM 응답 JSON 등).
- `tests/unit/conftest.py`는 수집 전 **세 가지 격리만** 한다 — HOME·XDG 홈 디렉터리를 일회용 경로로 돌리고(개발자 실홈 보호), 거부 스텁 `gws` 를 PATH 앞에 놓고(소유자 실 Google 계정 보호), **노드 상태**를 가린다(2026-10-19 — 릴리스 호스트가 주 노드 자체가 되자 main 이 바뀌지 않았는데도 324건이 실패했다). 노드 상태 격리는 기존 프로덕션 덮어쓰기 env(`AUTOPHAGY_SKILL_LIVE_ROOT`·`AUTOPHAGY_REPO_ROOT`·`AUTOPHAGY_SYSTEM_NODE_CONFIG`·각 게이트의 승인 로그·`HEALTHCHECK_OWNER_NOTICE_CREDENTIAL`·`TMPDIR`)를 일회용 루트 아래 **없는** 경로로 돌리고, 거부 스텁 `sudo` 를 같은 PATH 앞에 놓으며(운영자 계정은 agent·ops 로 무암호 sudo 다), 감사 훅이 실제로 있는 노드 경로(`/srv/autophagy*`·`/etc/autophagy`·`/var/lib/autophagy`·서비스 계정 홈)를 프로세스 안에서 열면 그 시험을 `UNIT-TEST-NODE-READ` 로 실패시킨다. 셋 다 import 시점이어야 하는 이유는 프로덕션 모듈이 자기 import 중에 `Path.home()`·`shutil.which("gws")`·위 env 를 이미 해석하기 때문이다. 고정은 `test_unit_node_isolation.py`. 그 밖에는 unit이 `tmp_path`/`monkeypatch`/stub 실행파일/env override 패턴을 직접 사용한다.

## 규칙
- **단위 시험은 병렬로 돈다**(2026-10-19부터, `-n auto`). 시험마다 어느 워커에서 어떤 순서로 돌지 정해지지 않는다 — 다른 시험이 먼저 돌았다는 것, 고정 경로·포트, 모듈 전역 상태에 기대지 않는다(임시 경로는 `tmp_path`). 병렬에서만 깨지는 시험은 고친다. 끄거나 건너뛰거나 바뀐 파일만 시험하지 않는다.
- **env 를 새로 만들어 자식을 띄우면 `unit_node.env()` 를 먼저 펼친다**(`env={**unit_node.env(), "HOME": ...}`) — 자식은 conftest 의 노드 격리를 물려받지 않아 노드에서 실제 설치를 다시 읽는다. 서비스 계정 홈(`/home/agent`)이나 노드 미러(`/srv/autophagy-agents`)를 가짜 값으로 쓰지 않는다: 노드에서는 그 경로가 실제로 있고 읽을 수 없다. 대신 `AUTOPHAGY_UNIT_NODE_ROOT` 아래 경로를 쓴다.
- **unit 테스트는 개발자 실홈의 상태(`~/.hermes` 등)를 읽거나 쓰지 않는다.** `conftest.py`가 HOME 을 바꾸고 `test_unit_home_isolation.py`가 그것을 고정한다. 그 교체는 `os.environ` 을 물려받는 자식에게만 닿으므로 **env 를 새로 만들어 자식을 띄우는 테스트는 `HOME` 도 넘긴다**(보통 `tmp_path`) — 빠지면 자식이 passwd 의 실제 홈으로 되돌아간다(2026-09-11 strace 실측: `test_recall_skill.py` 의 자식이 실제 `~/.hermes/config.yaml` 을 열었다).
- **FS3 정산 레코드가 고정한 테스트 파일에는 케이스를 더하지 않는다.** `.omo/evidence/fs3/completions/task-*.json` 의 `green`/`red` 는 특정 테스트 파일들의 **출력 해시**를 못박고 `tests/unit/test_fs3_replay_gate.py` 가 매번 재생을 대조한다 — 그 파일에 한 줄만 더해도 과거 RED/GREEN 증적이 재현되지 않는다(2026-08-26 실측: `test_watch_failure_streak.py`·`test_deploy_host_fail_closed.py`). 원장의 해시를 고쳐 맞추는 것은 증적 위조다. 새 검사는 **새 파일**에 두고 왜 갈라놨는지를 그 파일 docstring 에 적는다(선례: `test_watch_failure_streak_store.py`, `test_deploy_host_fail_closed_all.py`).
- **버그 수리는 RED→GREEN 회귀 고정 선행.** 특히 no-agent cron의 자식 subprocess 자격증명 전파는
  회귀로 못박는다 — 선례: `test_calendar_confirm_watch_subprocess.py`,
  `test_coordination_confirm_watch_subprocess.py` (부모 env에 토큰 없고 accessor만 해석 가능한 상황에서 자식이 토큰을 받는지 검증).
- 게이트/보안 경계(dori-gate, `effect_approval` 카드 검증, deadlock/재협상)는 mutation 로직 변경 시 반드시 단위 테스트 동반. Hermes 도구 분류기의 회귀는 은퇴했으며 저장된 카드의 해시·소유자 검증은 유지한다.
- 테스트는 실시크릿·실외부효과를 유발하지 않는다 — 격리(`E2E_TEST_MODE`/DUMMY 시크릿/서명 주입) 경로만 사용.
- **워크스테이션 PATH 의 `gws` 는 소유자 실 자격증명이다 — 워처 테스트는 실행 seam 을 반드시 가짜로 바꾼다.**
  `shutil.which("gws")` 로 바이너리를 찾는 스킬(todo·calendar·budget)의 승인 워처는 ✅ 뒤 쓰기까지
  같은 틱에서 실행하므로, ✅ 픽스처를 주고 runner/`run_gws` 를 주입하지 않으면 단위 테스트가 **실제 Google API 에 쓴다**.
  2026-09-11 실측: `test_todo_watch.py` 두 테스트가 2026-08-25(63f5456aa) 이후 전량 스위트마다 "합성 워처 과제"·"만료 직전"
  2건을 소유자 Google Tasks 에 등록해 1,387건이 쌓였다.
  **가드는 파일이 아니라 PATH 에 있다** — `conftest.py` 가 수집 전 거부 스텁 `gws` 를 PATH 앞에 놓고
  `test_gws_path_guard.py` 가 그것을 고정한다(네 스킬의 `gws_bin()` 이 전부 스텁으로 해석되는지까지). 파일 단위 autouse
  픽스처로는 **다른 파일·다른 스킬·자식 프로세스를 하나도 덮지 못한다**: 2026-09-12 실측으로 패치된 체크아웃이 깨끗한
  동안 형제 워크트리의 pre-patch 사본(`acc45485b`)이 전량 스위트를 돌려 같은 2건을 실제로 등록했다 — 노드가 "미적용
  상태와 동일" 로 본 것이 이것이다. 스텁은 argv 와 `PYTEST_CURRENT_TEST` 를 남기고 exit 97 로 거부하므로 어느 테스트가
  실 자격증명을 부르려 했는지 그 로그(`AUTOPHAGY_UNIT_GWS_GUARD_LOG`)로 특정된다.
  기존 `test_todo_watch.py::_no_real_gws` 는 **소리를 키우는 층**으로 남긴다 — `AssertionError` 는 워처의
  `except Exception` 이 `failed:` 결과로 삼키므로 `BaseException` 파생인 `pytest.fail` 이어야 한다.
