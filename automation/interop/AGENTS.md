# automation/interop/ — 외부효과 게이트 + 봇간 인터롭 코어

프로젝트에서 가장 안전-임계인 경계. 규약 단일 진실은 [docs/guide/interop-규약.md](../../docs/guide/interop-규약.md).
모든 mutating 툴콜은 여기를 통과해야 하며, 원칙은 **fail-closed + 해시 바인딩**.

## 파일 지도
| 파일 | 역할 |
|------|------|
| `external_effect_gate.py` | `evaluate_tool_call` — denylist(`configs/external-effect-tools.yaml`) 매칭. 미매칭=읽기 허용. 매칭(mutation)=owner 승인 레코드 필요. `action_hash=sha256(정규화 payload)`, method는 `manual_reaction` 또는 `signed_injection_e2e`만 |
| `approval_types.py` · `approval_reminder.py` · `approval_lease.ReminderJournal` | 기존 승인 레코드의 key·원문 message id·실제 게시 시각에 고정된 리마인더 계산. 기존 key lease 안에서 lifecycle 관찰→영속 claim→최소정보 원문 링크 전송하며 catch-up은 최신 due 구간 1건만 시도한다. 별도 승인 상태머신이 아니다 |
| `external_effect_gate_e2e.py` · `injection_adapter.py` | E2E 서명(HMAC) 승인 경로. `E2E_TEST_MODE=1`에서만 동작 |
| `hermes_plugin/` · `hook_boundary_e2e.py` | Hermes 플러그인 경계 — 게이트가 실제로 강제되는 지점. **게이트웨이가 부르는 훅은 `register()` 가 등록하는 여섯뿐이다**(`pre_gateway_dispatch`·`pre_tool_call`·`transform_llm_output`·`kanban_task_{claimed,completed,blocked}`) — "에러를 동반한 라이프사이클 실패" 훅은 벤더에 없다. 그래서 **라이프사이클 실패 → 수리 티켓 경로는 만들지 않는다(2026-09-09 결정)**: 붙일 훅이 없고, `#agents-log` 보고는 이미 `kanban_task_*` → `_send_kanban_report` 가 하며, 티켓 쪽을 지금 형태로 켜면 `record_lifecycle_failure` 의 90초 subprocess 가 async 훅 안에서 턴을 세우고 dedup location 이 `task_id` 라 실패한 task 마다 새 카드가 열리며 수리 카드가 blocked 로 생성되므로 `kanban_task_blocked` 에 걸면 자기 루프가 된다. 그 자리를 메우던 대체된 선행 구현 `hermes_hook.py` 는 같은 날 삭제했다 — 인프라 실패는 healthcheck detect 가, 소유자가 눈치챈 실패는 `!repair` 가 덮는다. `coord-` 질의 응답은 config의 `interop_channel_id` 채널(#autophagy-agents)로 라우팅되며, 미설정 시 소스 채널로 폴백. 승인 표면은 `approval_surface.py`가 결정하고 `approval_directory.py`가 해석한다 — 설정 키(`personal_approvals_channel_id` / `deploy_approvals_channel_id`)는 그 디렉터리 내부에서만 읽힌다 |
| `digest_thread.py` | 아침 다이제스트의 하루 한 표준 승인 스레드(2026-10-16 Q187) — 날짜별 바인딩 파일(0600, flock)을 메일·캘린더가 공유하고, `settle` 이 미결 카드 수로 🔔 / ✅ 완료 표시를 맞춘다(이름 변경은 `origin_notice.rename_thread`·`close_thread`). 승인 판정은 없다 |
| `approval_reminder_config.py` | `config.yaml`의 `approval_reminders`를 검증된 enabled·initial/repeat 모델로 로드(기본 true·3h·1h, 간격 오류 fail-fast) |
| `thread_pointer.py` | 승인 요청 CLI 가 게시 직후 내는 `APPROVAL-THREAD <키>=<값> url=<링크>` 줄의 단일 정의. 좌표는 레코드의 `approval_guild_id`·`approval_thread_id`, 링크는 `owner_message.discord_link`, 좌표 결손은 `url=unavailable search=<값>`(추측 금지). I/O 없음 |
| `discord_transport.py` · `chunker.py` | `DiscordTransport` 순차 청킹 전송 + 429 `Retry-After` 백오프. `chunk_message`=2000자 분할 |
| `coordination.py` | 에이전트간 일정 조율 **순수 상태머신**. 가용성 교집합→후보 ≤3→양측 승인→소유자 승인=캘린더 쓰기 게이트. `correlation_id`는 `coord-` 접두사 |
| `delegation.py` · `report.py` | 위임 봉투 / `#agents-log` 보고 포맷 |
| `killswitch.py` | `!pause-agents`/`!resume-agents` (소유자만, 영속) |
| `owner_proxy.py` | 소유자 대리 요청 — `interop_bot_prose` 의 유일한 예외. 사설 설정 `owner_proxy_bot_id`·`owner_proxy_origin_channel_id` 의 봇 하나가 `#agent-chat`(스레드 포함)에 옮겨 쓴 글을, 링크한 원문을 Discord API 로 읽어 소유자 작성·같은 guild·원문 채널(스레드)·24시간·1회(원장)를 모두 확인한 뒤에만 받는다. 확인 불가는 거부. 선택 키 `owner_proxy_origin_webhook_ids` 의 소유자 웹훅이 원문 채널 자체에 쓴 글도 원문으로 받는다(`via=owner-webhook`). 원문 검증이 실패해도 대리 봇의 접두어·본문 있는 글은 `admit` 이 대리 봇 요청(`via=dori`)으로 받는다(2026-10-10 소유자 지시). 다른 봇은 그대로 버린다. 접두어 이름은 `도리`(`PROXY_NAME`, 2026-10-11)이고 옛 `오모냥` 은 비권장으로 계속 받으며 수락 로그 `prefix=current|legacy|other` 로 구분한다. 승인 게이트·✅ 판정은 불변. I/O 어댑터(원장·원문 조회)는 `owner_proxy_io.py`. 규약 [interop §3.5](../../docs/guide/interop-규약.md) |
| `owner_proxy_watch.py` | 대리 요청 누락 안전망 — 거부된 대리 요청은 즉시, 받았는데 3분 안에 이 봇의 글이 없는 요청은 기한 뒤에 대리 글 스레드에 통지한다(게이트웨이 ✅ 는 버린 글에도 붙는 영수증이다). 대기 목록은 파일이라 재시동 뒤 `register()` 가 다시 무장한다. 게이트웨이 주 프로세스에서만 동작하고 아무것도 실행하지 않는다 |
| `voice_transcript.py` | 소유자 음성 메시지(2026-10-12 소유자 지시) — `pre_gateway_dispatch` 가 일시정지 확인 직후 부른다. 봇이 아닌 `owner_id` 작성자의 음성만 노드 whisper.cpp(`SPEECHTOTEXT_WHISPER_BIN`·`_MODEL`, env → `~/.env.secrets`, `-l ko`)로 전사해 `[음성 메시지 · 로컬 전사] <전사문>` 으로 rewrite 하고, 음성은 이벤트에서 제자리 제거해 Hermes 기본 STT(faster-whisper `base`) 가 다시 돌지 않게 한다. 실패는 `skip`(`voice_transcription_failed`) + 메시지 스레드·DM 통지. wav·캐시 원본 삭제, 전사문 비로깅, 끄기 `AUTOPHAGY_VOICE_TRANSCRIBE=0`. 훅은 동기라 전사 시간만큼(3.7초 음성 ≈ 1초) 이벤트 루프를 묶는다 — 그래서 10분 상한·단계별 120초 제한을 둔다. 규약 [interop §3.6](../../docs/guide/interop-규약.md) |
| `digest_reaction_listener.py` | 다이제스트 카드 📄/✍️ 를 누른 즉시 처리(2026-10-17 소유자 "반응이 너무 느려") — 훅이 아니다: Hermes 는 Discord 반응을 플러그인에 넘기지 않으므로 `register()` 가 데몬 스레드로 게이트웨이 러너(`gateway.run._gateway_runner_ref`)의 Discord 클라이언트에 `on_raw_reaction_add` 를 한 번 붙이고(재연결로 클라이언트가 바뀌면 다시 붙임) 소유자의 📄/✍️ 만 `~/.hermes/scripts/mail_triage_watch.py --digest-card <id>` 자식으로 넘긴 뒤 바로 돌아온다(이벤트 루프를 기다리게 하지 않음). 반응 재확인·소유자만·카드·동작당 한 번은 메일 스킬(`digest_reaction_actions.run_card`)이 2분 틱과 같은 claim 으로 지킨다. 게이트웨이 주 프로세스이고 메일 워처 파일이 있는 계정(agent)에서만 무장, 끄기 `DIGEST_REACTION_LISTENER=0`, 처리마다 `~/.hermes/mail-triage/logs/digest-reaction-events.jsonl` 한 줄 |
| `loop_guard.py` | 봇 연쇄 분당 5회 상한 + 본문 해시 dedup |
| `gate_driver.py` · `live_peer_driver.py` | 게이트/피어 드라이버 |
| `production_guard.sh` | 프로덕션 게이트웨이에서 `E2E_TEST_MODE` 거부(부팅 차단) |
| `free_response.py` | 게이트웨이 드롭인 `ExecStartPre=-…` 가 매 시작마다 `agent_chat_channel_id` 를 같은 계정 `config.yaml` 의 `discord.free_response_channels` 에 정확히 한 번 맞춘다(빠졌을 때만 한 줄 수정, 백업·원자 교체, ignored 우선, 키 없는 peer 는 무동작). doctor 「agent-chat 채널 무멘션 응답」이 같은 `decide`/`configured_channels` 를 읽기 전용으로 쓴다 |

## 불변식 (변경 전 반드시 확인)
- 공급망 승인 채널은 type 0이고 이름이 `approvals`이거나 설정 ID(또는 명시적 공급망 핀)와 같을 때 받는다 — 이름 변경을 받는 쪽으로만 넓혔고 예전 기록의 의미는 그대로다. `approvals`·`notifications`는 표시 이름 예시이며 변경할 수 있다. 설정 없는 레거시 공급망 조회만 이름 검색을 쓴다. 정책 버전·저장 형식은 불변이다.
- **mutation은 owner 승인 레코드 없이는 절대 실행 안 됨.** 승인 판정 로직(`_has_valid_approval`) 변경은 보안 회귀.
- **`coordination.py`는 부수효과 없는 순수 상태머신** — 캘린더 쓰기/전송은 드라이버(coordination 스킬)가 소유. deadlock 10분·재협상 정확히 1회 규칙은 회귀 테스트로 고정.
- `DiscordTransport` 호출부는 저장소 전반에 흩어져 있다(`grep -rl 'DiscordTransport(' --include=*.py`) — 시그니처를 바꾸면 그 목록 전체를 동반 갱신한다. 세어 둔 숫자는 금방 낡으므로 적지 않는다.
- 게이트/커버리지 부족 파일이 많음 — 로직 수정 시 RED→GREEN 단위 테스트 선행(`tests/unit/`).
- **`w1-5-*` 위임 트래픽은 반드시 소스 채널(#team) 응답을 유지해야 한다** — W1-5 게이트가 #team에서 response_availability를 폴링하므로 rerouting 금지. coord- 트래픽만 interop 채널로 분리한다.
- **승인 표면 해석**: 표면은 `approval_surface.py`(정책, I/O 없음)가 정하고 `approval_directory.py`(유일한 해석기)가 실제 채널로 바꾼다. 승인 producer가 스스로 채널을 해석하면 `tests/unit/test_approval_lifecycle_conformance.py`가 빌드를 깨뜨린다 — 산문이 아니라 코드가 강제한다.
  - **표면은 레코드에 영속된다**(`kind`/`surface`/`channel_id`/`policy_version`). 이후의 모든 읽기·리액션·삭제는 그 저장값을 쓴다. 정책이 앞서 나가도 저장된 레코드는 자기가 게시된 채널로 배수된다(append-only 전환 원장).
  - **현재 라우팅 (AS-SSOT, v8 + 요청별 스레드 — 2026-09-01 소유자 지시)**: calendar·coordination·wiki·mail compose·mail reply·budget·patent-export·obsidian-write·todo·repair(v8, ON-4 — Ops 봇 초대 전제) = **개인 서버 `#agent-chat`의 요청별 스레드**(`resolve_new_binding(request=RequestThread)` → `agent_chat_request_thread`: 지시 메시지가 이 채널 소속이면 앵커(400=message id 재사용), 아니면 안내 메시지를 먼저 게시하고 그 메시지에 앵커, 스레드 이름은 `<아이콘> <라벨> · <제목≤40>`, 스레드 첫 메시지 첫 줄은 `## <아이콘> <라벨> · <제목>`(루트 「스레드 제목 규칙」) — 조회 없음, 요청끼리 공유 없음; record.channel_id=스레드 id라 기존 리액션 워처·리마인더는 무변경 동작). 결과 통지는 레코드 `approval_thread_id` 로 같은 스레드에 게시하고 `origin_notice.ThreadOutcome` 으로 종결 표시(접두어+아카이브). kind별 `승인-<kind>` 스레드(v7, `agent_chat_thread` find-or-create)는 `request=None` 레거시 호출에만 남으며 범위 내 호출부는 `tests/unit/test_request_thread_adoption_conformance.py` 가 `request=` 를 강제한다. skill-deploy·peer-attest·skill-publish·personal-skill-submit·managed 활성화 = **개인 서버 `#approvals`**(2차 주체인 peer 봇 또는 그룹 관리자가 같은 채널을 봐야 함). v6 이하로 게시된 기존 DM pending 레코드는 저장 바인딩(S2)대로 DM에서 종결된다. **주의: `#approvals`에는 메일·과제비·캘린더·위키·수리 승인이 단 한 건도 게시된 적이 없다**(전 생애 117건 실측).
  - 흐름별 `*_APPROVALS_CHANNEL_ID` env override는 **AS-3.2에서 제거되었다** — 승인 표면은 `~/.hermes/interop/config.json`의 config 키(`personal_approvals_channel_id` → 캐시 → guild-scan / **`agent_chat_channel_id`는 config 키 단독, 미설정=fail-closed·스캔 없음**)로만 해석되며, 환경 변수로 표면을 옮길 수 있는 경로는 없다(`tests/unit/test_approval_lifecycle_conformance.py::test_no_flow_specific_approvals_env_var_is_read`가 재도입을 차단). `deploy_approvals_channel_id`(공급망 config 키)는 대상이 아니다.
- **`#agents-log` 보고 summary는 마스킹 필수** — 파일 경로·PII·시크릿·계정 식별자 보호(`report.py`, 원문: docs/guide/interop-규약.md).
- **`approval_lifecycle.request_owner_approval`은 보안 경계임**: 아래 5대 불변식을 약화하는 것은 소유자의 ✅가 무시되는 권한 회귀(Authorization Regression)로 간주함.
    - (1) Lease 보유 상태에서만 변이, (2) `message_id` 절대 덮어쓰기 금지, (3) 결정된 요청(APPROVED/CANCELLED) 파괴 금지, (4) 불확실 시 생존 간주(`UNVERIFIABLE` → `DEFER`), (5) 비성공 시 명시적 Reason + non-zero exit.
- **주의**: `_probe`의 예외 처리를 임의로 좁히지 마십시오. Lease 파일을 `unlink` 하거나 delete/drop 순서를 바꾸지 마십시오.
- **리마인더는 기존 watcher tick에서만 실행합니다.** 새 confirm surface·리액션·resolver·병렬 watcher를 만들지 않습니다. 본문은 `⏰ 승인 대기 N시간 · <승인 카드 링크>` 한 줄이며 좌표 미상은 링크만 생략합니다. 같은 틱·같은 목적지는 `dispatch_owner_approval_reminders`로 메시지 하나에 카드별 한 줄씩 묶습니다. 원문 채널 밖으로 전송 범위를 넓히지 않으며 승인·취소 반응 기호는 넣지 않습니다.
- **리마인더 claim은 전송 오류에도 해제하지 않습니다.** 원격 전달 후 오류인지 구분할 수 없어 해제하면 동일 구간 중복 전송이 가능해집니다. 실패한 구간은 at-most-once로 닫고 다음 wall-clock 구간부터 다시 시도합니다.
- **I/O 제약**: `approval_lifecycle` 및 `approval_lease`는 lease/journal 파일 외의 디스크, 네트워크, 환경 변수에 직접 접근하지 않습니다. 저장소와 Discord 클라이언트는 각 게이트가 Protocol을 통해 주입해야 하며, 여기에 직접 I/O를 추가하지 마십시오.
