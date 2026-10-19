# healthcheck 폭주 정지 — 재귀 기록 스윕 제거 + 겹친 틱 양보

결함은 **둘**이었고 둘 다 고쳤다.

1. **재귀 기록 스윕(폭주의 원인)** — 허용목록 기록이 자기 자신을 다시 부르고 있었다.
2. **겹침 가드 부재(폭주를 키운 조건)** — 느린 sweep 하나가 돌 때 다음 cron 틱이 그대로 겹쳐
   쌓였다. 이제 `flock` 으로 양보한다.

## 무엇을
### 1. 기록 스윕은 자기 프로브를 부르지 않는다
`healthcheck_probe_wrapper.sh --inputs-digest` 는 허용목록을 **관측**으로 만든다(모든 체크를 한
바퀴 돌려 나가는 명령을 기록). 그 목록 안에 래퍼 드리프트 프로브 자신이 있었고, 그 프로브는
기대 지문을 얻으려 생성기를 **새 프로세스로** 실행했다 — 그 프로세스가 다시 기록 스윕을 돌리는
무한 재귀다. 이제 기록 루프가 `HEALTHCHECK_WRAPPER_RECORDING=1` 을 export 하고,
`probe_healthcheck_wrapper_current` 는 그 값이 보이면 노드에 낼 명령만 남기고 rc 0 으로
돌아온다(생성기 미호출). 판정은 평시 틱이 그대로 한다.

### 2. 겹친 틱은 양보한다
`automation/healthcheck.sh` 가 sweep 을 시작하기 전에 lock 을 잡는다(`flock -n`).

- **잡히면** 종전대로 sweep 을 돈다.
- **못 잡으면**(앞선 sweep 이 아직 도는 중) sweep 을 **시작하지 않고** stderr 에
  `HEALTHCHECK-OVERLAP-SKIP` 한 줄만 남기고 exit 0 으로 양보한다. 로그 파일도 만들지 않는다 —
  로그가 없다는 것이 "프로브가 돌지 않았다"의 증적이다.
- **lock 파일을 열 수조차 없으면** `HEALTHCHECK-LOCK-UNAVAILABLE` 을 남기고 exit 1 로 멈춘다.

lock 기본 경로는 `<runtime-logs>/healthcheck/healthcheck.lock`, 재정의는
`HEALTHCHECK_LOCK_FILE` 이다. `automation/pipeline_lock.py`·`managed_sync` 의 FileKeyLease 와
같은 양보 규약을 shell 에서 그대로 따른다.

## 왜
ops crontab 은 healthcheck 를 `*/5` 로 부른다. 그런데 최근 400 회 실행의 중앙값은 **4048 초**
(p90 14760 초, 최대 39020 초)로 틱 간격보다 한 자릿수 길었다. 왜 한 번이 몇 시간씩 걸렸나 —
위의 재귀 때문이다. 노드 실측(2026-08-31)에서 cron 실행 **2 개가 ops 프로세스 436 개**로 불어나
있었고 전부 `bash .../healthcheck_probe_wrapper.sh --inputs-digest <node>` 였다. 노드는 memory
pressure critical 에 닿았고, 재귀로 망가진 기대 지문 때문에 허용목록 체크는 영구 FAIL 이었다
(티켓 `t_d2ac107a` 약 1946 회). 재귀는 523371d6(프로브 명령 해시를 래퍼 지문에 접는다)에서
들어왔다. 단위 테스트가 이를 못 본 이유는 구조적이다 — 워크스테이션에는 릴리스 소스 루트 아래
생성기가 없어 프로브가 `WRAPPER-DRIFT-UNKNOWN` 으로 조기 반환했다.

겹침 가드가 없었으므로 그 느린 sweep 위로 틱이 계속 쌓였고, 같은 날 **동시 실행 114 개**가
관측됐다. 재귀를 고치면 sweep 은 다시 빨라지지만, 그것만으로는 "느린 sweep 이 겹치지 않는다"를
보장하지 못한다 — 그래서 `flock` 은 원인 제거가 아니라 **다시는 쌓이지 않게 하는 안전벨트**다.

그 폭주 아래에서 SSH 프로브가 간헐적으로 타임아웃하며 **거짓** 수리 티켓을 냈다.

| 티켓 | 내용 | 보고한 실행 |
|---|---|---|
| `t_2578c8ed` | healthcheck failure: `<primary-node>` LiteLLM | 16:30:02Z 시작 → 18:25:23Z 보고 |
| `t_2524fe33` | healthcheck failure: … peer hermes-gateway.service | 같은 실행 |

같은 두 프로브는 다른 **모든** 실행에서 PASS 였다. 즉 서비스는 멀쩡했고 폭주가 만든 지연이
관측을 오염시켜 없는 장애를 신고한 것이다. 모니터가 자신이 만든 부하로 거짓 신고를 하면
수리 경로 전체의 신뢰가 깎이므로, 고칠 지점은 프로브 타임아웃이 아니라 **틱이 겹치는 것 자체**다.

양보를 실패(non-zero)로 두지 않은 것도 같은 이유다 — cron 실패 관측이 정상 동작으로 붉어지면
그 신호도 함께 못 쓰게 된다.

## 사용 시나리오
**착지 후 1 회 — 래퍼 재생성(운영자 조치)** — 기록이 결정적으로 바뀌면서 허용목록의 입력 지문도
바뀐다. 노드에서 한 번 재생성해야 래퍼 드리프트 체크가 PASS 로 돌아온다(root 불필요).

```bash
f=$(mktemp) && sudo -n -u ops -H bash /srv/autophagy-agent-current/automation/healthcheck_probe_wrapper.sh --print <primary-node> > "$f" && bash /srv/autophagy-agent-current/automation/provision-healthcheck-probe.sh --from-file "$f" <primary-node>; rm -f "$f"
```

헬스체크 계정(`ops`)이 생성한다 — 드리프트 프로브는 그 계정의 시야로 판정하고, 비공개 루트의 은퇴 표식은 그 계정만 읽는다. 운영자가 `--install` 로 만들면 은퇴한 행이 남아 지문이 영영 맞지 않는다(2026-10-19 노드 실측).

**틱이 조용히 넘어간다(happy path)** — 긴 sweep 이 도는 동안 다음 틱이 뜨면 그 틱은 즉시
끝난다. 확인은 lock 소유자 쪽 로그가 **하나만** 늘어나는 것으로 한다.

```bash
ssh <primary-node> 'sudo -n -u ops ls -1 /srv/autophagy-private/runtime-logs/healthcheck/ | tail -3'
```

양보한 틱의 흔적은 cron 메일/`stderr` 의 `HEALTHCHECK-OVERLAP-SKIP a previous sweep still
holds <lock>` 한 줄뿐이다.

**lock 을 열 수 없다(실패 경로)** — 로그 디렉터리 권한이 깨졌거나 `HEALTHCHECK_LOCK_FILE` 이
만들 수 없는 경로를 가리키면 `HEALTHCHECK-LOCK-UNAVAILABLE path=<경로>` 를 남기고 **exit 1**
로 멈춘다. 열리지 않는 lock 은 "아무도 안 돈다"의 근거가 될 수 없기 때문이다(fail-closed).
이때는 sweep 이 아예 돌지 않으므로 경로·권한을 먼저 고친다.

## 관련
- `automation/healthcheck_probe_wrapper.sh`(기록 스윕) · `automation/healthcheck_wrapper_probe.sh`
  (재귀 가드) · `automation/healthcheck.sh`(flock 가드)
- `docs/guide/operations.md` 「3. 읽기 전용 healthcheck」
- 같은 규약의 선례: `automation/pipeline_lock.py` · `automation/managed_sync`(FileKeyLease)
- 회귀 `tests/unit/test_healthcheck_wrapper_recursion.py` — 기록 모드 생성기 미호출 · 평시 호출 ·
  `--inputs-digest` 가 마감 안에 끝나고 결정적임을 고정한다(재귀가 살아 있으면 마감 초과).
- 회귀 `tests/unit/test_healthcheck_overlap_guard.py` — 양보(rc 0 + 마커 + 로그 0 개) ·
  단독 틱 정상 sweep · lock 미개방 fail-closed 세 경로를 고정한다.
- 발단 티켓: `t_2578c8ed` · `t_2524fe33`(둘 다 거짓 신고로 판정) · `t_d2ac107a`(재귀로 망가진
  기대 지문 때문에 약 1946 회 반복된 허용목록 FAIL).
