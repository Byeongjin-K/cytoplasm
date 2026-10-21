# rag_ingest — W2-4 개인 RAG 인제스트 파이프라인

`personal_cha` 벡터 컬렉션(W2-1 MCP memory server 경유)에 개인+팀 지식을
적재하는 파이프라인. **임베딩은 설정된 RAG 노드 내부 로컬 bge-m3 서버에서만**
일어난다 — 이 패키지는 어떤 임베딩 API도 직접 호출하지 않고, 콘텐츠를 MCP
`load_memory` 툴로 전달할 뿐이다.

## 소스

| source_type | 원천 | source_key |
|---|---|---|
| `wiki` | `~agent/wiki/*.md` (W2-2 볼트) | `wiki:<relpath>` |
| `note` | `~agent/notes/*.md` | `note:<relpath>` |
| `meeting` | `~agent/notes/meetings/*.md` (W2-3 원문 저장 위치) | `meeting:<relpath>` |
| `conversation` | Hermes `state.db` (read-only) — 세션×KST일 단위 LLM-free 다이제스트 | `conversation:<session>:<day>` |
| `peer-report` | #agents-log의 active roster principal이 보낸 W1-6 v0 보고(정확 7키 JSON 블록) | `agents-log:<message_id>` |
| `team-chat` | #team 신규 메시지 배치 트랜스크립트 | `team:<first_id>-<last_id>` |
| `obsidian` | `~agent/.hermes/obsidian-mirror` (git-obsidian read-only 미러) | `obsidian:<relpath>` |

모든 벡터에 **내 관점 메타데이터**(`agent_id/owner/role/project/interest_tags`
+ `source_type` + 출처 필드)가 붙는다. Markdown frontmatter의 `created`, `modified`,
`updated`, `date`도 보존한다. Obsidian은 YAML 또는 `>[!info]` callout에 명시된 날짜만
`event_date`/`document_updated`(ISO date)로 정규화한다. 파일 mtime은 쓰지 않으며,
경로 날짜는 `date_basis=path`로만 표시하고 `event_date`로 승격하지 않는다.
`notes/research-trends/`는 적재하지 않으며 남은 옛 벡터는 지운다(주간 연구동향 보고서와 키워드는 공도리가 맡는다). 팀 지식의 인별 중복 적재는 의도된 설계(사용자 결정
2026-07-13). W2-5 recall은 `source`/`metadata`(위키 경로, `task_id`, `message_id`,
`session_id`)로 출처를 표기할 수 있다.

`peer-report`는 Discord 실제 작성자 ID를 roster의 admin publisher principal 또는 active
member node label로 해석한 값과 본문 `agent_id`가 정확히 일치할 때만 적재한다. 미등록·
removed 작성자와 불일치 보고는 본문 없는 warning만 남기고 문서 생성 전에 거부하므로
`queue.jsonl`과 MCP `load_memory`에 들어가지 않는다. roster가 없거나 깨졌으면 그 tick의
Discord source 전체를 fail-closed로 건너뛴다.

## 멱등성 (2중)

1. **클라이언트 fingerprint** — `state.json`에 문서별 청크 (source,content)
   해시를 기록. 무변경 문서는 네트워크 호출 0으로 스킵.
2. **서버 uuid5 upsert** — MCP 서버가 `uuid5(source\ncontent)`를 포인트 id로
   사용하므로 강제 재적재(`--force`)도 동일 포인트를 덮어써 중복 0.

문서 변경 시 이전 포인트 중 새 청크에 없는 id는 `delete_memory`로 정리된다.

## 장애 경로 (큐잉/재시도, 유실 0)

변경분은 먼저 `queue.jsonl`에 내구 잡으로 기록된 뒤 전달된다. RAG 노드가
다운이면 잡이 큐에 남고 다음 cron tick에 재시도된다. state/커서는 전달 성공
후에만 전진한다(at-least-once + 멱등 upsert = 유실 0, 중복 0).

obsidian 미러 sync 실패는 파이프라인을 중단하지 않는다(Codified decision 2):
WARN 로그 후 last-good 미러가 있으면 그대로 스캔(신선도 희생 수용), 없으면
그 tick의 obsidian 소스만 스킵한다. 스킵된 tick은 deletion sync를 건드리지
않으므로 기존 벡터가 지워지지 않는다.

## 배포 (설정된 primary agent)

- 배포 경로: `automation/rag_ingest/deploy.sh`. `origin/main` provenance를 확인한 뒤
  패키지 전체를 `~/.hermes/rag_ingest_runtime/rag_ingest/`에 tar로 전개하고 원격
  파일 수·핵심 해시를 read-back한다. `cron/`은 패키지에서 제외한다.
- 설정: `~/.hermes/rag-ingest/config.json` (600; `config.example.json` 참조 —
  guild id 등은 repo에 두지 않는다)
- 시크릿: `~/.env.secrets`의 `RAG_MCP_API_KEY`(ops 핸드오프), `DISCORD_BOT_TOKEN`
- 워처: 배포기가 `cron/rag_ingest_watch.py`를
  `~/.hermes/scripts/rag_ingest_watch.py`로 별도 배포하고, 없을 때만
  `hermes cron create "every 10m" --name rag-ingest-watch --no-agent --script rag_ingest_watch.py --deliver local`
  을 실행한다. 워처와 배포기는 같은 `~/.hermes/rag-ingest/watch.lock`을 쓴다.
  첫 Obsidian 부트스트랩처럼 긴 tick 중 배포는 최대 300초 기다리고, 락을 얻지
  못하면 기존 패키지를 건드리지 않은 채 실패한다.

## 실행

```bash
python3 -m rag_ingest run [--config PATH] [--sources wiki,notes,...] [--force] [--verbose]
```

exit 0 = 정상(큐잉 포함), exit 1 = 설정/인증 오류. 성공 tick은 무음(stdout 없음),
백로그가 남으면 한 줄 공지. 상세 로그는 `~/.hermes/rag-ingest/logs/`(600) —
설치별 정보가 제목에 포함될 수 있어 repo/공개 채널로 복사 금지(제약 8).

## 테스트

```bash
cd /path/to/repo && python3 -m pytest tests/unit/test_rag_ingest_*.py -v
```
