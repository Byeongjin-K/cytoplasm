# DGX Spark 2대 활용 검토 — 전면 삭제(wipe) 후 autophagy 전환 사전 검토

> **과거 검토 기록**: 아래 LiteLLM 존치·재설치·별칭 추가 제안은 2026-10-19 소유자
> "이거 아예 삭제해 litellm" 결정으로 폐기했다. 비용 보고도 "이것도 이제 없애라" 결정으로
> 제거했다. 당시 비교와 근거만 보존하며 현재 실행할 계획이 아니다([제거 소개](기능소개/litellm-지우기.md)).

> **목적**: 기존 OpenClaw·음성 인식 시스템을 양 노드에서 전부 삭제하고 `.omo/plans/autophagy-agents.md`를 적용하기 전에, ① Spark 2대가 필요한지 ② 1대만 필요하다면 나머지 1대를 어떻게 쓸지(2026 최신 트렌드 기반)를 판정한다.
> **입력**: `.omo/plans/autophagy-agents.md`(v1.1), `docs/hardware-infra-openclaw.md`, 웹 리서치(2025-10 ~ 2026-07 출처, §7).
> **작성**: 2026-07-13.

---

## 0. 결론 요약

| 질문 | 결론 |
|---|---|
| **Q1. Spark 2대가 필요한가?** | **아니오.** autophagy 계획은 로컬 LLM 서빙이 없는 API 전용 시스템이라 GPU 사용 0, 상주 메모리 <10GiB — **1대 용량(128GB)의 10% 미만**. HA 요구도 없음. 2대째는 "필요"가 아니라 "활용 기회". |
| **Q2. 나머지 1대는?** | **"프라이빗 AI 랩 노드"로 운용 권장**: ① 에이전트 샌드박스·스테이징(계획 W1-8/W6-2와 직결) ② 프라이빗 LLM 추론(특허 민감 콘텐츠 완전 온프렘) ③ 파인튜닝·음성·미디어 실험(수요 발생 시). **2대를 클러스터로 묶는 것은 비권장**(200B+ 모델을 실사용할 때만 가치, §5). |
| **(부수) wipe 전 확인** | 계획서 v1.1은 "기존 시스템과 additive 공존"을 전제로 작성됨 → wipe 시 **계획서 수정 필수 지점 7곳**(§3.3) + **백업 목록**(§3.2) + **삭제 범위 결정 1건**(LiteLLM/Qdrant 존치 여부, §3.1). |

---

## 1. Q1 — Spark 2대가 필요한가?

### 1.1 계획서의 실제 리소스 풋프린트

autophagy 계획(v1.1)이 상주시키는 컴포넌트 전량:

| 컴포넌트 | 근거 (계획서) | 자원 추정 |
|---|---|---|
| Hermes 인스턴스 5개 (멤버당 1) | W1-2/W1-3/W2-1 | Python/Node 프로세스, 각 ~0.3–1GiB |
| ops 수리 에이전트 | W6-2 | 동급 프로세스 1개 |
| Kanban 보드 (Hermes 내장 또는 Planka/Vikunja) | W1-4, W0-7 | 컨테이너 ~0.5–1GiB |
| LiteLLM 게이트웨이 + Postgres | W1-1 | 컨테이너 ~1–1.5GiB |
| cron·폴러 (리마인더/메일/헬스체크) | W1-7, W3-2, W4-2 | 무시 가능 |
| 스킬 샌드박스 인스턴스 (임시 기동) | W1-8 | 일시적 ~1GiB |
| **합계** | | **정상 상태 <10GiB, GPU 0** |

결정적 근거는 계획서 자신의 스코프 제외 조항:

- OUT: **"로컬 LLM 서빙(vLLM 등) — 사용자 명시 제외"** → GPU·대용량 메모리 수요가 원천적으로 없음
- OUT: "실시간 음성 STT 없음(v1 제외)" → Whisper급 상주 모델도 없음
- 하드웨어 문서 §6: "autophagy 추가분 = 수 GiB~10GiB급. 로컬 LLM 추론 없음(API 전용)이므로 GPU/대용량 메모리 불요"

### 1.2 1대 용량 대비

- 노드 1대 = `<memory-per-node>` unified memory + `<cpu-capacity>` + `<accelerator-model>`.
- wipe 후 빈 노드 기준 가용 메모리 `<available-memory-range>` → autophagy 상주분은 **한 노드 용량의 일부**.
- 계획 W0-2의 배치 게이트(MemAvailable ≥40GiB)는 wipe 후 **자동 충족**. (이 게이트 자체가 "기존 vLLM이 노드1 메모리를 다 먹고 있다"는 v1.1 당시 제약의 산물이다.)

### 1.3 계획에서 2대째가 기여하는 바

- **고가용성(HA) 요구 없음**: 계획의 최고 수준 복구 요구는 W6-5 "재부팅 복구가 문서만으로 완료" — 5인 연구실 톨러런스로 핫스탠바이가 요구되지 않음.
- **분산 처리 없음**: 에이전트간 협업은 Discord/Kanban/git 경유 메시징이지 노드간 컴퓨팅이 아님.
- 유일한 간접 수요 = W1-8 샌드박스 격리·W6-2 회귀 실행인데, 같은 노드의 계정 격리로도 충족된다(계획이 그렇게 설계됨). 유휴 노드가 있으면 **더 좋아지는** 항목일 뿐(§4.2).

### 1.4 판정

**1대로 충분하며 여유가 크다.** 커뮤니티 컨센서스도 동일: 이 워크로드(클라우드 API 기반 멀티에이전트, ~20GiB 미만)에는 2대를 묶을 이유가 없고, "클러스터의 가치는 200B~405B 로컬 모델·대형 KV 캐시·2노드 파인튜닝을 실제로 쓸 때만" 발생한다(§5 실측 근거).

---

## 2. 노드 배정 제안 (wipe 후)

wipe로 v1.1의 배치 제약(노드1 메모리 고갈 → `<rag-node>` 강제)이 소멸하므로 배치 노드를 다시 선택할 수 있다.

| | **Option A (권장): 프로덕션=`<primary-node>`, 랩=`<rag-node>`** | Option B: 계획서 그대로 (프로덕션=`<rag-node>`, 랩=`<primary-node>`) |
|---|---|---|
| SSH | cha → `<primary-node>` **직접 SSH** — 계획서의 중첩 SSH 표준 래퍼(`ssh <primary-node> 'ssh <rag-node>.local …'`) 대부분 제거 가능 | 중첩 SSH 래퍼 유지 (운영 마찰 지속) |
| 네트워크 인그레스 | `<primary-node>`은 기존 cloudflared/게이트웨이 인그레스 노드였음 — 검증된 경로 | 인그레스 전례 없음 |
| 계획서 수정량 | 배치 노드 명칭 변경 필요 (W0-2/W0-3 등) — 단, §3.3의 wipe 수정과 **같은 개정판(v1.2)에서 일괄 처리** | 노드 명칭 수정 불필요 |
| 실험 격리 | 실험 노드(`<rag-node>`)가 OOM/드라이버 행으로 죽어도 프로덕션 무영향 | 동일 (역방향) |

**원칙이 핵심**: 어느 쪽이든 "**프로덕션 에이전트 노드**"와 "**실험/GPU 랩 노드**"를 물리적으로 분리하라. 실험 워크로드(파인튜닝, 대형 모델 로딩)는 노드를 죽이는 일이 잦고, 에이전트 시스템은 24/7 상주 서비스다.

---

## 3. 전면 삭제(wipe) 영향 검토 — 적용 전 필수 확인 3건

### 3.1 삭제 범위 결정 (사용자 결정 필요)

"기존 openclaw 및 음성 인식 시스템 모두 삭제"의 엄밀한 범위를 먼저 확정해야 한다:

| 대상 | 명백히 삭제 | 비고 |
|---|---|---|
| 노드1: vLLM(qwen3.6-27b)·SSE-Repack·OpenClaw(18789)·telegram-gateway·cloudflared·워치독 | ✅ | 개인 시스템 본체 |
| 노드2: Whisper STT(9000) | ✅ | 음성 인식 |
| 노드2: **LiteLLM(4000)+Postgres** | ⚠️ **결정 필요** | 계획 W1-1은 "기존 게이트웨이 additive 확장(신규 배포 아님)" 전제. 삭제하면 **신규 배포로 회귀**(기존 013_openclaw patch 문서가 배포 절차 레퍼런스로 유효하므로 난도는 낮음) |
| 노드2: bge-m3(8001)·Qdrant(6333) | ⚠️ **결정 필요** | cha_wiki RAG 전용. autophagy 계획은 사용 안 함 → 개인 위키 RAG를 계속 쓸지에 따라 결정 |

### 3.2 삭제로 사라지는 기능 + 백업 권장 목록

**사라지는 것(인지하고 삭제할 것):** 개인 텔레그램 봇 전체(아침일보 08:30, 돌봄비 cron, 비용 리포트), cha_wiki RAG 검색, 음성 전사, 로컬 qwen3.6-27b 추론.

**삭제 전 백업(스냅샷 1회, 총 수 GB 이내):**

1. `<primary-node>:<primary-secret-path>`, `<rag-node>:<rag-secret-path>` — 시크릿(프로바이더 키는 autophagy에서 재사용 가능)
2. `~/.openclaw/` (설정·세션·cron 정의 — 아침일보 프롬프트 등 재구현 시 참조)
3. LiteLLM Postgres 덤프 (spend 이력 — 비용 추적 연속성 원하면)
4. Qdrant 컬렉션 스냅샷 4종 (`mail_similarity`, `wiki_rag`, `post_dedup`, `vault_bge_m3_v1`) — 재임베딩 비용 절약
5. 양 노드 `crontab -l`, systemd user unit 목록, `~/services/docker-compose.yml`
6. telegram-gateway 설정 (봇 토큰·webhook)

### 3.3 계획서 v1.1 → v1.2 수정 필요 지점 (계획은 "additive 공존" 전제로 작성됨)

| # | 위치 | v1.1 (현행) | wipe 후 (v1.2) |
|---|---|---|---|
| 1 | TL;DR·Scope IN | "기존 OpenClaw 클러스터에 **additive**로 추가" | "클린 클러스터에 신규 설치" |
| 2 | OUT 조항 | "기존 OpenClaw 개인 시스템의 파괴적 변경 금지(additive-only)" | **삭제** (보호 대상 소멸) |
| 3 | W0-2 | 기존 서비스 헬스체크 베이스라인 + 설정 표면 해시 캡처(`configs/baseline/`) | 클린 인벤토리만(`uname/free/df/ss/curl`). MemAvailable≥40GiB 게이트는 자동 충족 |
| 4 | W1-1 | "기존 litellm-gateway **additive 확장**(신규 배포 아님)", 기존 7종 무변경 diff 검증, 무중단 변경 절차 | **신규 LiteLLM 배포** + 신규 별칭 5종·가상키·예산캡만. 기존 7종 검증·무중단 절차 삭제 |
| 5 | W0-8 | "노드1 기존 운영 OpenClaw(18789)와 완전 분리" 주의 | 불필요 (기존 인스턴스 없음) |
| 6 | F4 | "기존 시스템 무중단·무변경 검증(헬스체크 베이스라인 대조 + 기존 7종 1콜 + 기존 cron 정상)" | **삭제** 또는 "구 서비스 잔존 프로세스/포트 0건" 확인으로 대체 |
| 7 | 배치 노드 | 기본 `<rag-node>`(노드1 메모리 고갈 사유) | 사유 소멸 → §2 결정에 따라 명칭 일괄 수정 |

추가: `docs/hardware-infra-openclaw.md`는 wipe 시점부로 **역사 문서**가 되므로, W0-2 산출물(`configs/inventory.md`)이 신규 기준선임을 명시할 것.

---

## 4. Q2 — 유휴 Spark 1대, 어떻게 쓸까 (2026 트렌드 리서치)

### 4.0 옵션 개관 — 트렌드 강도 × autophagy 시너지

2026년 7월 기준 웹 리서치(NVIDIA 공식 playbook, 커뮤니티 실측, 트렌드 리포트) 종합:

| 순위 | 역할 | 2026 트렌드 강도 | autophagy 시너지 | 초기 노력 |
|---|---|---|---|---|
| 1 | 에이전트 샌드박스·스테이징 노드 | ★★★ (2026 최대 트렌드) | ★★★ (W1-8/W6-2 직결) | 낮음 |
| 2 | 프라이빗 LLM 추론 (특허 민감 온프렘) | ★★★ (커뮤니티 1위 용도) | ★★★ (보안 강화) | 중간 |
| 3 | MCP·메모리·RAG 서버 | ★★★ (MCP 생태 폭증) | ★★ (팀 지식베이스) | 중간 |
| 4 | 파인튜닝·실험 랩 (LoRA/QLoRA) | ★★ | ★~★★ | 중간 |
| 5 | 음성 스택 부활 (회의 전사 v2) | ★★ | ★★ (v2 기능) | 낮음~중간 |
| 6 | 이미지/비디오 생성 | ★(혼합) | ★ (발표자료 도해) | 중간 |
| 7 | 2노드 클러스터링 | 니치 | 없음 | 높음+불안정 |

> 리서치 총평: *"가장 좋은 extra AI box는 chat 서버가 아니라 ① 에이전트 샌드박스+MCP/메모리 서버 ② 로컬 코딩 에이전트 런타임 ③ 배치/스탠바이/CI 노드"* — 단일 Spark의 커뮤니티 정체성은 "CUDA 네이티브 로컬 AI 랩 박스"(프라이빗 모델 서버, 에이전트 샌드박스, 파인튜닝 노드)이지 처리량 머신이 아니다.

### 4.1 [1순위] 에이전트 샌드박스·스테이징 노드

**2026년 가장 뜨거운 패턴**: 에이전트 실행을 격리된 로컬 박스에서 돌리는 self-hosted sandbox. Anthropic이 관리형 에이전트용 self-hosted sandbox 문서를 공식 제공하고, NVIDIA는 Spark playbook에 OpenShell(에이전트 실행 격리)을 올렸으며, `agentctl`(2026-06)·`agentbox`(2026-01) 같은 도구가 연이어 등장했다.

**autophagy와의 시너지가 가장 직접적**:
- **W1-8 스킬 게이트**: "샌드박스 인스턴스에서 시나리오 실행 통과 후 장착" — 이 샌드박스를 유휴 노드에 두면 프로덕션 노드와 물리 격리(불량 스킬이 프로덕션 리소스/시크릿에 접근할 표면 자체가 없어짐).
- **W6-2 수리 에이전트**: "샌드박스에서 기존 뱅크 100% + 신규 GREEN 확인 후 적용" — 회귀 뱅크 전체 실행을 유휴 노드에서.
- **E2E 스테이징**: `E2E_TEST_MODE=1` 회귀 뱅크(F3) 무인 실행 환경을 프로덕션과 분리.
- **보너스**: NVIDIA 공식 dgx-spark-playbooks에 **hermes-agent와 openclaw playbook이 둘 다 존재**(2026-06-12 갱신) — 계획의 메인/폴백 프레임워크 스택을 유휴 노드에 그대로 재현 가능하다는 뜻.

### 4.2 [2순위] 프라이빗 LLM 추론 노드 — 특허 민감 콘텐츠 완전 온프렘

**커뮤니티에서 단일 Spark의 실사용 1위**: 프라이빗 모델 서버. Simon Willison이 Spark에서 `gpt-oss:120b`를 Codex CLI 백엔드로 상시 운용(Tailscale 경유)하는 것이 대표 사례이고, NVIDIA도 CLI coding agent playbook(Claude Code/Codex CLI 레시피 포함)을 공식 제공한다.

**이 연구실에 특별히 강한 이유 — 보안 업그레이드**:
현 계획의 특허 보호는 "patent-sensitive → **GLM(중국계) 금지**, sonnet-5로 라우팅"인데, sonnet-5도 결국 **외부(미국 클라우드) 전송**이다. 유휴 Spark에서 오픈 모델을 서빙하고 LiteLLM에 `local-private` 별칭을 추가하면 **특허·발명 콘텐츠의 외부 전송이 0건**이 된다. LiteLLM은 OpenAI-호환 로컬 엔드포인트 등록만으로 라우팅되므로 계획의 승급 사다리에 그대로 끼워 넣을 수 있다.

**모델 후보 (2026-07 기준, 128GB 단일 노드 fit 확인)**:

| 모델 | 구성 | 컨텍스트 | 비고 |
|---|---|---|---|
| **NVIDIA Nemotron-3-Super-120B-A12B-NVFP4** | 120B total / 12B active MoE | **1M** | 모델카드가 "Minimum GPU: 1× DGX Spark" 명시 — 가장 Spark-native. vLLM 실측 22.7–23.7 tok/s |
| **gpt-oss-120b** | 117B / 5.1B active, MXFP4, Apache 2.0 | 128k | 실측 38–41 tok/s(llama.cpp/Ollama). 가장 무난한 품질/속도 |
| **Qwen3-Coder-30B-A3B** / Qwen3-32B | 30B MoE / 32B dense | 262k / 32–131k | 체감 속도 최상 균형(코딩·구조화 추출용) |
| GLM-4.5-Air | 106B / 12B active, MIT | — | 성능 강하나 Spark 최적화 양자화 체크포인트 미확인(워치리스트). GLM-4.6은 357B로 부적합 |
| Qwen3-Next-80B-A3B | 80B / 3B active | 262k–1M | 양자화 체크포인트 확보 시에만 고려 |

- **서빙 스택**: 단순함 우선이면 llama.cpp(GGUF), 범용 OpenAI-호환은 vLLM, 최고 성능은 TRT-LLM(+**EAGLE-3 speculative decoding**으로 대역폭 병목 완화 — gpt-oss-120b 공식 레시피 존재).
- **속도 기대치 관리**: Spark의 약점은 273GB/s 메모리 대역폭 — dense 70B q4는 4.4 tok/s로 실용 불가, **MoE 모델(위 표)이 유일한 정답**. 20–40 tok/s는 특허 초안·분류·요약 같은 백그라운드 파이프라인엔 충분하고, 실시간 대화 메인 모델로는 부족.
- ⚠️ **계획 정합성**: 계획 OUT에 "로컬 LLM 서빙 없음(사용자 명시)"이 있으므로, 이 옵션은 **v1 스코프 밖 — 유휴 노드의 독립 용도**로 시작하고, 채택 확정 시 v1.2에서 OUT 조항과 W1-1 라우팅 표(`local-private` 별칭 추가)만 수정하면 된다. W5-5(특허 스킬) 착수 전에 결정하는 것이 이상적.

### 4.3 [3순위] MCP·메모리·RAG 서버

MCP 생태가 2026년 사실상 표준으로 굳는 중(State of MCP 2026: npm 다운로드 330만+, 공개 MCP repo 12,000+; MCP Census 2026-07: 서버 15,382개). 유휴 노드를 **팀 공유 도구 서버 + RAG 검색 박스**로 두는 패턴.

- autophagy 시너지: 계획의 공유 메모리(W2-2)는 git 기반 YAML/MD라 별도 서버가 불필요하지만, **삭제되는 cha_wiki RAG(bge-m3+Qdrant)를 "팀 지식베이스 RAG"로 부활**시켜 에이전트들이 MCP/스킬로 조회하게 하는 확장이 자연스럽다(백업해 둔 Qdrant 컬렉션 재활용).
- 단, v1 범위에는 없으므로 W5(연구동향·산출물) 안정화 후 검토.

### 4.4 [4순위] 파인튜닝·실험 랩

NVIDIA 공식 playbook이 Unsloth·LLaMA Factory·NeMo AutoModel·PyTorch 파인튜닝을 Spark에서 직접 지원. 실용 상한: **LoRA/QLoRA ≤ 70B**(공식 예제 확인), full fine-tune은 3–8B가 현실적. FLUX.1-dev 12B LoRA(이미지) 예제도 있음.

- 시너지 아이디어: 연구실 도메인 문서로 소형 모델 SFT, 에이전트 대화 로그 기반 tuning 실험.
- 냉정한 평가: 리서치 결과 "실사용 트렌드는 맞지만 1순위는 아님 — 내부 데이터가 많고 반복 튜닝 수요가 있을 때만 ROI" (실제 2026 RL/trajectory tuning 사례는 더 싼 클라우드 GPU에서 도는 경우가 많음).

### 4.5 [5순위] 음성 스택 부활 — 회의 전사 서버 (v2)

지금 삭제하는 Whisper의 상위 호환으로 되살리는 경로. 2026년엔 Whisper large-v3-turbo 외에 NVIDIA Parakeet/Canary 계열이 transformers에 통합되어 선택지가 넓어짐. 계획 W2-3(회의자료 인제스트)은 v1에서 "음성 제외"지만, **회의 녹음 → 전사 → 기존 회의록 파이프라인 투입**은 가장 자연스러운 v2 확장이고 유휴 노드 GPU가 정확히 필요한 지점.

### 4.6 [선택] 이미지/비디오 생성

실측: FLUX.2 ComfyUI 이미지 ~60초/장, LTX-2 720p 비디오 ~3분(Tom's Hardware, 2026-01). 발표자료(W5-3)용 도해 생성 정도의 수요면 가능하나, 전용 용도로는 ROI 낮음 — 필요할 때 랩 노드에 띄우는 온디맨드 워크로드로 취급.

### 4.7 [기본 비권장] 2노드 클러스터링 유지

§5 참조. **결론: 묶지 말고 독립 운용, 200GbE 직결 케이블만 유지**(비용 0, 필요할 때 몇 분 안에 클러스터 재구성 가능).

### 4.8 권장 로드맵

| 단계 | 시점 | 유휴 노드 상태 |
|---|---|---|
| 0 | wipe ~ W1 게이트 통과 | **전원 off(콜드 스탠바이)도 유효** — 운영 표면 최소화. 필요 시 W0-8 폴백 스모크용으로만 기동 |
| 1 | W1-8(스킬 게이트) 착수 | **샌드박스·스테이징 노드**로 상시 기동 (§4.1) |
| 2 | W5-5(특허 스킬) 착수 전 | **프라이빗 추론** 채택 여부 결정 → 채택 시 gpt-oss-120b(무난) 또는 Nemotron-3-Super-120B(장문) 서빙 + LiteLLM `local-private` 별칭 (§4.2) |
| 3 | 수요 발생 시 | RAG 지식베이스 부활 / 회의 전사 v2 / 파인튜닝 실험 (§4.3–4.5) |

---

## 5. 참고: 2노드 클러스터링 실측 데이터 (왜 기본 비권장인가)

NVIDIA 공식 스토리는 "2대 연결 = 256GB 결합 메모리, 최대 405B 모델"이지만, 실측과 공식 문서의 세부는 다음과 같다:

| 항목 | 실측/공식 | 출처·시점 |
|---|---|---|
| Llama 3.1 405B | vLLM playbook이 **"testing only — production headroom 부족"** 경고 | NVIDIA playbook, 2026-03 |
| Qwen3-235B GPTQ-Int4, 2노드 vLLM TP=2 | **17 tok/s**(batch=1), 36.4 tok/s aggregate(batch=4), 기동 ~15분 | Conselara Labs, 2026-05 |
| Qwen3-235B, SGLang | **TP=2는 NCCL deadlock으로 hang 빈발**, PP=2만 동작(12 tok/s) | NVIDIA Forum, 2026-04 |
| DeepSeek V4 Flash 2노드 | 튜닝 후 ~41 tok/s single-stream / ~350 tok/s aggregate(c=32) | elsung repo, 2026-06 |
| 200GbE 링크 품질 | RDMA 185–195Gb/s, NCCL 23–24GB/s, DDP 효율 93.5% — **링크 자체는 우수** | ArgentAIOS, 2026-04 |
| 커뮤니티 체감 | 1→2대 = 1.7–1.9× 스케일링, "2대가 개인용 sweet spot — **단, 그 이득을 실제로 쓸 때만**" | NVIDIA Forum, 2026-06 |
| 2노드 파인튜닝 | FSDP+LoRA로 70B까지 공식 지원 | NVIDIA Tech Blog, 2026-01 |

**해석**: 링크는 훌륭하지만, 2노드가 여는 것은 "200B+ 모델을 10~40 tok/s로 굴리는 능력"이고 이 연구실의 현재 계획엔 그 수요가 없다. 스택 안정성 문제(TP=2 hang)도 여전해 상시 결합 운용은 운영 부담. **필요해지면 그때 묶는다** — 케이블·NCCL 설정은 이미 검증돼 있어 재구성 비용이 낮다.

---

## 6. 후속 액션 제안 (이 문서의 결론을 실행으로 옮길 때)

1. **[결정]** §3.1 삭제 범위: LiteLLM+Postgres 존치(계획 그대로 확장) vs 전부 삭제 후 신규 배포 / Qdrant·bge-m3 운명(cha_wiki 지속 여부)
2. **[결정]** §2 노드 배정: Option A(프로덕션=`<primary-node>`) 권장
3. **[결정]** §4.2 프라이빗 추론 채택 여부 (W5-5 전까지만 결정하면 됨)
4. **[작업]** §3.2 백업 실행 → wipe → 계획서 v1.2 개정(§3.3의 7개 지점 + 노드 명칭) → W0-1부터 착수
5. 개인 시스템 기능(아침일보 등)을 폐기할지, 추후 autophagy 위에 재구현할지는 별도 결정(본 검토 범위 밖)

---

## 7. 출처

**NVIDIA 공식**
- DGX Spark System Overview — https://docs.nvidia.com/dgx/dgx-spark/system-overview.html (2026-07-09 갱신)
- DGX Spark Playbooks(전체) — https://github.com/NVIDIA/dgx-spark-playbooks : vLLM(2026-06-12) · SGLang(2026-04-28) · TRT-LLM(2026-04-28) · llama.cpp(2026-06-03) · Speculative Decoding(2026-04-20) · Unsloth(2025-12-15) · LLaMA Factory(2026-02-18) · NeMo fine-tune(2026-03-04) · FLUX finetuning(2025-11-07) · **hermes-agent(2026-06-12)** · **openclaw(2026-06-12)** · OpenShell(2026-06-12) · connect-two-sparks(2025-11-24) · multi-sparks-through-switch(2026-03-19) · CLI coding agent(2026-04-16) · multi-agent chatbot(2025-11-20) · RAG AI Workbench(2025-10-28) · NIM(2025-12-22) · Ollama(2025-10-12)
- NVIDIA Tech Blog, 2-Spark 최적화(256GB 결합, Qwen-235B NVFP4, 70B FSDP+LoRA) — https://developer.nvidia.com/blog/new-software-and-model-optimizations-supercharge-nvidia-dgx-spark/ (2026-01-05)

**실측·리뷰**
- Ollama 공식 Spark 벤치마크(gpt-oss-20b 58.3 / 120b 41.1 / llama3.1-70b q4 4.4 tok/s) — https://ollama.com/blog/nvidia-spark-performance (2025-10-23)
- llama.cpp Spark 성능 스레드(gpt-oss-120b 38.5 tok/s) — https://github.com/ggml-org/llama.cpp/discussions/16578 (2025-10-14~)
- LMSYS/SGLang Spark 분석 — https://lmsys.org/blog/2025-10-13-nvidia-dgx-spark/ (2025-10-13)
- vLLM 공식 Spark 블로그(Nemotron-3-Super 120B 22.7–23.7 tok/s, TP=2 조건) — https://vllm.ai/blog/2026-06-01-vllm-dgx-spark (2026-06-01)
- ServeTheHome 리뷰 — https://www.servethehome.com/nvidia-dgx-spark-review-the-gb10-machine-is-so-freaking-cool/ (2025-10-14)
- Tom's Hardware 리뷰(FLUX.2 ~60s, LTX-2 720p ~3min) — https://www.tomshardware.com/pc-components/gpus/nvidia-dgx-spark-review (2026-01-27)
- Simon Willison, Codex CLI + gpt-oss:120b on Spark — https://simonwillison.net/2025/Nov/7/codex-spark-gpt-oss/ (2025-11-07)
- Devashish, 단일 Spark 멀티모델 에이전트 서버 — https://www.devashish.me/p/two-qwen3-models-on-one-dgx-spark (2026-06-16)
- Conselara Labs, 2노드 Qwen3-235B 벤치마크 — https://conselara.dev/notes/dgx-spark-benchmarks/ (2026-05-09)
- Tobias Weiss, 2노드 DeepSeek V4 클러스터 — https://www.tobias-weiss.org/content/ai/2-node-dgx-spark-deepseek-v4-cluster/ (2026-05-31)
- elsung, DeepSeek V4 Flash 듀얼 Spark — https://github.com/elsung/dgx-spark-deepseek-v4-flash (2026-06-14)
- ArgentAIOS, Spark 클러스터 가이드(RDMA/NCCL/DDP 실측) — https://github.com/ArgentAIOS/dgx-spark-cluster (2026-04-09)
- NVIDIA Forum, NCCL deadlock(TP=2 hang) — https://forums.developer.nvidia.com/t/nccl-all-reduce-deadlock-on-dual-dgx-spark-after-successful-channel-establishment-affects-both-vllm-and-trt-llm/366127 (2026-04-13)
- NVIDIA Forum, "1→2 Sparks" 스케일링 체감 — https://forums.developer.nvidia.com/t/going-from-1-2-sparks/373831 (2026-06-19)
- Hacker News Spark 토론 — https://news.ycombinator.com/item?id=45586776 (2025-10-15)

**모델 카드**
- gpt-oss-120b — https://openai.com/index/introducing-gpt-oss/ (2025-08-05) · https://huggingface.co/openai/gpt-oss-120b
- Nemotron-3-Super-120B-A12B-NVFP4("Minimum GPU: 1× DGX Spark") — https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 (2026-03-11)
- Qwen3-Coder-30B-A3B — https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct · Qwen3-Next-80B-A3B — https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct
- GLM-4.5-Air — https://huggingface.co/zai-org/GLM-4.5-Air

**트렌드(에이전트·MCP·음성)**
- Anthropic self-hosted sandboxes — https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes (2026-07 접근)
- agentctl — https://github.com/qtnx/agentctl (2026-06-08) · agentbox — https://github.com/tsilva/agentbox (2026-01-22)
- State of MCP 2026 — https://mcp.institute/research/state-of-mcp-2026 (2026-03-15) · MCP Census — https://mcpcensus.pages.dev/report (2026-07)
- OpenAI Codex CLI — https://github.com/openai/codex (2026-07-13 릴리스) · Qwen3-Coder — https://github.com/QwenLM/Qwen3-Coder
- Parakeet in transformers — https://github.com/huggingface/transformers (2026-07 접근)
