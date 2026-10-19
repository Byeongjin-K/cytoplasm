# automation/interop/ — 승인 레코드·프로토콜 공용 코어

승인 레코드·카드·원문 통지와 프로토콜 값 형식을 공유한다. 규약은 [docs/guide/interop-규약.md](../../docs/guide/interop-규약.md).
Hermes 외부효과 평가기·플러그인·규칙 파일은 폐기했다. `deploy.sh`는 기존 `retired_jobs` 정리 경로만 호출하며 Hermes를 설치·활성화·재시동하지 않는다. `policy=retired`는 잔존 관측만 하고, 양 계정의 실제 파일 정리는 `retired-interop-v1` 필수 derived 선언이 맡는다. 게이트웨이가 정지·비활성임을 확인하지 못하면 삭제하지 않는다.
메일·예산·릴리스·외부 참석자 초대 카드와 기존 승인 저장 형식은 유지한다. 아래 소유자 대리 요청·음성·import-farm 도우미는 레거시 소비자·진단용이며 Hermes 훅으로 등록하지 않는다.

## 파일 지도
| 파일 | 역할 |
|------|------|
| `channel_move.py` · `approval_directory.channel_override` | 삭제 전 미결 승인 조회(GET·파일 읽기만)와 ops 채널 override 판정. 노드 설정 `private_root/channel-override.json`은 ops 소유·0600 일반 파일·17~20자리 두 채널 ID일 때만 기존 설정보다 우선한다. 홈 경로는 `ProtectHome` 수리 유닛에서 보이지 않아 쓰지 않는다. 부재는 기존 경로, 오류는 값 없는 경고 후 기존 경로다. 승인 판정·lifecycle은 바꾸지 않는다 |
| `effect_approval.py` | 카드 생산자가 고정한 `target_id`·도구 인자·소유자·방법에 묶인 기존 승인 JSONL 검증과 격리 E2E 서명 기록. 해시·챌린지·저장 형식은 그대로며 도구 분류·denylist 평가·Hermes 등록은 없다 |
| `approval_types.py` · `approval_reminder.py` · `approval_lease.ReminderJournal` | `approval_types`에는 레거시 저장용 `ApprovalContext`·결과 값 `ExternalEffectDecision`·`JsonValue`만 두며 승인 평가 구현은 없다. 기존 승인 레코드의 key·원문 message id·실제 게시 시각에 고정된 리마인더 계산. 기존 key lease 안에서 lifecycle 관찰→영속 claim→최소정보 원문 링크 전송하며 catch-up은 최신 due 구간 1건만 시도한다. 별도 승인 상태머신이 아니다 |
| `digest_thread.py` | 아침 다이제스트의 하루 한 표준 승인 스레드(2026-10-16 Q187) — 날짜별 바인딩 파일(0600, flock)을 메일·캘린더가 공유하고, `settle` 이 미결 카드 수로 🔔 / ✅ 완료 표시를 맞춘다(이름 변경은 `origin_notice.rename_thread`·`close_thread`). 승인 판정은 없다 |
| `approval_reminder_config.py` | `config.yaml`의 `approval_reminders`를 검증된 enabled·initial/repeat 모델로 로드(기본 true·3h·1h, 간격 오류 fail-fast) |
| `thread_pointer.py` | 승인 요청 CLI 가 게시 직후 내는 `APPROVAL-THREAD <키>=<값> url=<링크>` 줄의 단일 정의. 좌표는 레코드의 `approval_guild_id`·`approval_thread_id`, 링크는 `owner_message.discord_link`, 좌표 결손은 `url=unavailable search=<값>`(추측 금지). I/O 없음 |
| `discord_transport.py` · `chunker.py` | `DiscordTransport` 순차 청킹 전송 + 429 `Retry-After` 백오프. `chunk_message`=2000자 분할 |
| `coordination.py` | 에이전트간 일정 조율 **순수 상태머신**. 가용성 교집합→후보 ≤3→양측 승인→소유자 승인=캘린더 쓰기 게이트. `correlation_id`는 `coord-` 접두사 |
| `delegation.py` · `report.py` | 위임 봉투 / `#agents-log` 보고 포맷 |
| `killswitch.py` | `!pause-agents`/`!resume-agents` (소유자만, 영속) |
| `owner_proxy.py` | 소유자 대리 요청 — `interop_bot_prose` 의 유일한 예외. 사설 설정 `owner_proxy_bot_id`·`owner_proxy_origin_channel_id` 의 봇 하나가 `#agent-chat`(스레드 포함)에 옮겨 쓴 글을, 링크한 원문을 Discord API 로 읽어 소유자 작성·같은 guild·원문 채널(스레드)·24시간·1회(원장)를 모두 확인한 뒤에만 받는다. 확인 불가는 거부. 선택 키 `owner_proxy_origin_webhook_ids` 의 소유자 웹훅이 원문 채널 자체에 쓴 글도 원문으로 받는다(`via=owner-webhook`). 원문 검증이 실패해도 대리 봇의 접두어·본문 있는 글은 `admit` 이 대리 봇 요청(`via=dori`)으로 받는다(2026-10-10 소유자 지시). 다른 봇은 그대로 버린다. 접두어 이름은 `도리`(`PROXY_NAME`, 2026-10-11)이고 옛 `오모냥` 은 비권장으로 계속 받으며 수락 로그 `prefix=current|legacy|other` 로 구분한다. 승인 게이트·✅ 판정은 불변. I/O 어댑터(원장·원문 조회)는 `owner_proxy_io.py`. 규약 [interop §3.5](../../docs/guide/interop-규약.md) |
| `owner_proxy_watch.py` | 레거시 대리 요청의 미응답 감시·중복 통지 방지 도우미. Hermes 플러그인 등록은 폐기했으며 새 감시 스레드를 자동으로 시작하지 않는다 |
| `voice_transcript.py` | 레거시 소유자 음성의 로컬 전사·캐시 정리 도우미. Hermes 훅 호출은 폐기했고 독립 변환 계약만 보존한다 |
| `digest_reaction_listener.py` | 레거시 Hermes 반응 리스너 도우미. 플러그인의 자동 install 호출은 폐기했다. 메일 워처의 카드 판정·2분 틱은 별도 경로로 보존한다 |
| `loop_guard.py` | 봇 연쇄 분당 5회 상한 + 본문 해시 dedup |
| `gate_driver.py` · `live_peer_driver.py` | 레거시 프로토콜 진단 드라이버(자동 배포하지 않음) |
| `production_guard.sh` | 프로덕션 게이트웨이에서 `E2E_TEST_MODE` 거부(부팅 차단) |
| `free_response.py` | 계정 free-response 설정의 순수 판정·레거시 수리 도우미. doctor가 같은 판정을 읽으며 interop 드롭인의 부팅 적용은 폐기했다 |

## 불변식 (변경 전 반드시 확인)
- 노드 발신 토큰은 `automation.notice_bot.resolve_token`이 단독 해석한다. 활성 릴리스 override는 agent·peer 홈 또는 ops `private_root`의 계정 소유 0600 파일이며, 새 승인 목적지는 같은 override의 유돌 채널이다. `approval_lifecycle`·`origin_notice`의 주입 계약에는 토큰·파일 I/O를 넣지 않는다. 저장된 승인 좌표·결정은 전환 때문에 다시 쓰지 않는다.
- 공급망 승인 채널은 type 0이고 이름이 `approvals`이거나 설정 ID(또는 명시적 공급망 핀)와 같을 때 받는다 — 이름 변경을 받는 쪽으로만 넓혔고 예전 기록의 의미는 그대로다. `approvals`·`notifications`는 표시 이름 예시이며 변경할 수 있다. 설정 없는 레거시 공급망 조회만 이름 검색을 쓴다. 정책 버전·저장 형식은 불변이다.
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
