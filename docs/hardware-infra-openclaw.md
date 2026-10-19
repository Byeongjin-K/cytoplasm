# OpenClaw 하드웨어·인프라 현황 (autophagy 구성 기준 문서)

> **과거 기준선**: 아래 표·명령은 2026-07-13 기록이며 현재 운영 지침이 아니다.
> 2026-10-19 소유자 "이거 아예 삭제해 litellm", 비용 보고 "이것도 이제 없애라" 결정으로
> LiteLLM·PostgreSQL 비용 장부 서비스·09:00 보고는 폐기했다. 확장·재설치·점검 명령을 실행하지 않는다.
> 백업과 제거 범위는 [소개](기능소개/litellm-지우기.md)를 따른다.

> **목적**: autophagy 멀티에이전트 시스템을 **기존 `<accelerator-cluster>`에 additive로 구성**하기 위한 현황 기준선.
> 계획서(`.omo/plans/autophagy-agents.md`)의 W0-2(인벤토리 실측·배치 노드 확정)와 F4(기존 시스템 무중단 검증)가 이 문서를 참조한다.
> **작성**: 2026-07-13, 비공개 인프라 문서 조사 기반(출처는 §10). 문서 기준 스냅샷이므로 W0-2에서 실측으로 재검증할 것.

---

## 1. 클러스터 개요

| 항목 | 값 |
|---|---|
| 구성 | `<accelerator-node-model>` × `<node-count>` (`<accelerator-architecture>`) |
| Head 노드 | `<primary-node>` (mDNS `<primary-node>.local`) — 모든 명령 실행 진입점 |
| Worker 노드 | `<rag-node>` (mDNS `<rag-node>.local`) — 직접 SSH 불필요, Head 경유 |
| 메모리 | 노드당 `<memory-per-node>` unified (총 `<cluster-memory>`). head 실측 `<measured-memory>` |
| 인터커넥트 | `<interconnect-speed>` 직결, 내부망 `<private-interconnect-cidr>` (주소는 비공개), `<collective-library-version>` |
| 외부 네트워크 | Wi-Fi **DHCP — IP 수시 변경**. 고정 IP 의존 금지, mDNS `.local`로 접속 |
| OS | `<linux-distribution-version>`, **aarch64(ARM64)**, kernel `<kernel-version>` (`<primary-node>` 확인) |
| 스토리지 | root `<root-device>` `<filesystem>`, 암호화 `<encryption-state>` (총 용량은 W0-2에서 `df -h` 실측) |
| 소프트웨어 | OpenClaw `<openclaw-version>`, Node.js `<nodejs-version>`, vLLM `<vllm-version>` (`<vllm-image>`) |

**SSH 관행**: `~/.ssh/config`에 `.local` 호스트명 사용(IP 변경 자동 추종). `<primary-node>`에 `LocalForward <control-ui-port> localhost:<control-ui-port>`(Control UI). 노드 사용자는 `<operator-account>`. IP 변경 시 `ssh-keygen -R <hostname>`으로 known_hosts 정리. 노드2 접근: `ssh <primary-node> 'ssh <rag-node>.local "..."'`.

## 2. 노드별 서비스 맵 (2026-04-30 재편 + 2026-05-04 봇 마이그레이션 이후)

### 노드 1 — `<primary-node>` (Head): LLM 추론 + 게이트웨이 + 메시징 진입점

| 서비스 | 프로세스/컨테이너 | 포트 | 내용 |
|---|---|---|---|
| vLLM | `<inference-container>` | `<inference-port>` | `<inference-model>` (`<model-memory>`, `<parallelism>`), `<context-and-cache-settings>` |
| SSE-Repack 프록시 | `<proxy-script>` | `<proxy-port>` | OpenClaw→vLLM 연결 (`baseUrl http://127.0.0.1:<proxy-port>/v1`) |
| OpenClaw 게이트웨이 | `openclaw` | `<control-ui-port>` | Control UI + 기존 개인 메시징 봇 진입점 |
| 메시징 게이트웨이 | systemd user unit (`<operator-account>`) | `<messaging-port>` (127.0.0.1) | 봇 게이트웨이 |
| cloudflared-tunnel | systemd user unit | — | 텔레그램 webhook 인그레스 |
| vllm_watchdog.sh | cron `@reboot` | — | 추론+프록시 포트 `<health-interval>` 헬스체크, `<failure-threshold>`회 실패 시 자동 재시작 |

### 노드 2 — `<rag-node>` (Worker): AI 보조 서비스 (`<service-compose-path>`)

| 서비스 | 컨테이너 | 포트 | 내용 |
|---|---|---|---|
| Whisper STT | `whisper-stt` | `<stt-port>` | `<stt-model>`, 음성→한국어 전사 |
| 임베딩 | `vllm-embeddings` | `<embedding-port>` | `<embedding-model>` `<embedding-dimensions>` |
| 벡터 DB | `qdrant-db` | `<vector-db-port>` | 컬렉션 이름은 비공개 런타임 설정에서 관리 |
| LiteLLM 게이트웨이 | `litellm-gateway` | `<litellm-port>` | 단일 `/v1/*` 진입점 (§3) |
| PostgreSQL | `postgres-litellm` | (compose 내부 `<postgres-port>`, 호스트 미노출) | LiteLLM spend logs 비용 추적 |
| 노드2 워치독 | cron `<watchdog-interval>` | — | 서비스 헬스체크 |
| 알림 서비스 | (계획 문서만 존재) | `<notification-port>` 예정 | **UNCERTAIN — 실배포 확인 문서 없음. W0-2에서 `ss -tlnp`로 확인** |

### 점유 포트 요약 (autophagy 신규 포트 배정 시 회피 목록)

- **노드1**: `<primary-node-port-map>` (+cloudflared 아웃바운드)
- **노드2**: `<rag-node-port-map>` (+compose 내부 DB; 알림 포트 미확인)

## 3. LiteLLM 게이트웨이 (`<rag-node>:<litellm-port>`) — autophagy가 확장할 대상

| 항목 | 값 |
|---|---|
| 이미지 | `<litellm-image>` |
| 인증 | `LITELLM_MASTER_KEY` — `<rag-node>:<litellm-secret-path>` |
| DB | postgres-litellm (spend/logs 활성) |
| **기존 라우팅 집합 (무변경 대상)** | `<local-model-routes>` · `<external-model-routes>` |

**autophagy 확장 규칙(계획서 W1-1)**: 신규 별칭 `glm-4.7`/`glm-5`/`sonnet-5`/`gpt-5.6-terra`/`opus-4.8` + 멤버 가상키 5개 + 예산캡을 **추가만** 한다. 기존 7종 라우팅·키·설정은 diff로 무변경 검증, 변경 전 설정 백업+원복 절차를 patch 문서에 기록.

## 4. cron·자동화 현황 (기존 — 무중단 보존 대상)

| 잡 | 주기 | 위치 | 비고 |
|---|---|---|---|
| 아침일보 (OpenClaw cron `cf7d3a77…`) | 매일 08:30 | OpenClaw | cloud(Claude Sonnet 4.5) 라우팅 |
| 돌봄비 (`7f39fd78…`) | — | OpenClaw | cloud 라우팅 |
| 비용 리포트 | 매일 09:00 | 자동화 시스템 | LiteLLM spend 기반 |
| vllm_watchdog.sh | `@reboot`+30초 루프 | 노드1 system cron | 8000/11435 감시 |
| SSE-Repack 자동시작 | `@reboot sleep 60` | 노드1 system cron | |
| 노드2 워치독 | 5분 | 노드2 | 서비스 4종 |

> ⚠️ **호스트 OS TZ = Etc/UTC**. 모든 신규 cron은 `TZ=Asia/Seoul` 명시 또는 Python `zoneinfo` 사용(기존 아침일보 TZ 사고 전례 있음).

## 5. 계정·시크릿·배포 관행 (기존)

| 항목 | 값 |
|---|---|
| 노드 사용자 | `<operator-account>` (양 노드), 구형 호스트 계정은 비공개 |
| 서비스 유저 전례 | `<legacy-service-account>` (no-sudo) — **autophagy의 `agent-<id>`/`ops` 계정 설계와 동일 철학** |
| systemd | user unit + `loginctl enable-linger` (메시징 게이트웨이, cloudflared가 이 방식) |
| 시크릿 | `<primary-secret-path>`(봇/비용), `<rag-secret-path>`(LiteLLM 키), `<legacy-secret-path>`(0600) |
| 배포 경로 전례 | `<legacy-service-root>` (서비스 루트 소유 분리), `<legacy-symlink-paths>` |
| OpenClaw 인증 | 토큰 `<openclaw-config-path>`, Control UI `http://localhost:<control-ui-port>/#token=…` (SSH 포워드) |

## 6. 자원 현황과 autophagy 배치 시사점 ★

| 노드 | 메모리 상황(문서 기준) | 판단 |
|---|---|---|
| `<primary-node>` (Head) | MemTotal `<primary-total-memory>`, **MemAvailable `<primary-available-memory>`** (추론 서비스 상주) | **에이전트 배치 부적합** — 신규 상주 프로세스 금지 |
| `<rag-node>` (Worker) | 보조 서비스만 상주 — 문서상 실측치 없음 | **배치 기본 후보** — W0-2에서 `free -g` 실측, **MemAvailable ≥ `<minimum-available-memory>` 게이트** |

- autophagy 추가분(추정): Hermes/OpenClaw 인스턴스 여러 개 + Kanban 보드 + 샌드박스 = `<estimated-agent-memory>`. 로컬 LLM 추론 없음(API 전용)이므로 GPU/대용량 메모리 불요.
- 노드2 배치 시 이점: LiteLLM과 동일 노드(로컬 호출), 기존 상주 부하 낮음. 제약: SSH가 Head 경유 — provision 스크립트는 `ssh <primary-node> 'ssh <rag-node>.local …'` 패턴 사용.
- 노드1의 기존 OpenClaw와 **W0-8 폴백용 OpenClaw 신규 설치는 완전 분리**(ops 계정 전용, 포트 비충돌).

## 7. 재부팅 복구 순서 (기존 — autophagy 복구 문서가 이어받을 골격)

1. env 확인 → 잔여 컨테이너 정리 → 2. 노드1 vLLM 컨테이너 기동(`start_vllm.sh`, 워밍업 `<warmup-duration>`) → 3. SSE-Repack → 4. OpenClaw 게이트웨이 → 5. 노드2 `<service-compose-command>` → 6. 메시징 게이트웨이/cloudflared user unit 확인(linger로 자동)

## 8. 클러스터 전체 헬스체크 (W0-2 베이스라인·F4 대조용 one-liner)

```bash
ssh <primary-node> '
echo "[노드 1] vLLM     :"; curl -sf http://localhost:<inference-port>/v1/models > /dev/null && echo OK || echo FAIL
echo "[노드 1] Repack   :"; curl -sf http://localhost:<proxy-port>/v1/models > /dev/null && echo OK || echo FAIL
echo "[노드 1] OpenClaw :"; curl -sf http://localhost:<control-ui-port>/ > /dev/null && echo OK || echo FAIL
echo "[노드 2] Whisper  :"; curl -sf http://<rag-node>.local:<stt-port>/docs > /dev/null && echo OK || echo FAIL
echo "[노드 2] Embedding:"; curl -sf http://<rag-node>.local:<embedding-port>/health > /dev/null && echo OK || echo FAIL
echo "[노드 2] Qdrant   :"; curl -sf http://<rag-node>.local:<vector-db-port>/healthz > /dev/null && echo OK || echo FAIL
echo "[노드 2] LiteLLM  :"; curl -sf http://<rag-node>.local:<litellm-port>/health/readiness > /dev/null && echo OK || echo FAIL
'
```

## 9. 미기록·불확실 (W0-2 실측 항목)

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| 노드별 디스크 총 용량/여유 | 문서 미기록 | `df -h` 양 노드 |
| 노드2 실측 메모리 여유 | 문서 미기록 | `free -g` (`<minimum-available-memory>` 게이트) |
| 알림 서비스 실배포 여부 | 계획 문서만 존재 | `ss -tlnp \| grep <notification-port>` |
| MAC 주소/DHCP 예약 | 라우터 관리페이지 확인 필요로만 기록 | 라우터 admin |
| 노드2 OS 상세(커널 등) | `<primary-node>`만 확인됨 | `ssh <rag-node>.local 'uname -a'` |

## 10. 출처 (비공개 vault: `<private-vault-source-path>`)

- `instructions/인프라-정보.md` — 클러스터 HW/SW/네트워크, 노드 역할, 시크릿 계약, cha_wiki RAG
- `guide/DGX Spark 클러스터 노드별 구성 가이드.md` — 노드별 서비스/포트/헬스체크(2026-05-01)
- `instructions/참고-사항.md` — 프로바이더 라우팅, cron TZ, 워치독
- `patch/2026-04-23-노드-IP-재할당-및-SSH-config-변경.md`, `troubleshooting/2026-04-23-클러스터-네트워크-단절.md` — DHCP/mDNS/SSH
- `patch/2026-04-30-{Qwen3.6-단일노드-전환, 노드2-Whisper, 노드2-임베딩-Qdrant, LiteLLM-게이트웨이-배포, Telegram-게이트웨이-골격}.md`
- `<vllm-cache-tuning-patch>`, `<bot-migration-and-database-patches>`
- `<private-cluster-operations-guides>`
