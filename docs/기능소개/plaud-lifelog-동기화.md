# Plaud lifelog 동기화 (plaud_sync)

## 무엇을

PLAUD 녹음기의 전사·AI 요약을 **녹음 건별 Obsidian 노트**로 가져온다 — 2026-09-04 부터 v2 양식이다:
소유자 Linter 형식 frontmatter → `## 한눈에`(녹음·주제·사람·장소·한 줄) → `## 요약` → `## 결정 · 할 일` →
접힌 `## 전문` → 출처([소개](plaud-lifelog-노트-v2-양식.md)). MCP 서버를 에이전트에 등록하지 않는다 — no-agent cron 이
`npx @plaud-ai/mcp@0.3.10`(버전 고정) 프로세스와 stdio JSON-RPC 로 직접 대화하므로 **안 쓸 때의
컨텍스트 비용이 0** 이다. 저장은 건별 소유자 ✅ 를 거쳐 기존 obsidian_write(승인 해시 바인딩 + 원격
read-back)로 push 되고, RAG obsidian 소스가 자동 인제스트해 recall 검색으로 찾는다.

## 왜

소유자는 Plaud 를 lifelog 로 쓰고 그 내용을 에이전트가 보고 대응하길 원한다(features.md 의
'life log by PLAUD' 아이디어). MCP 를 게이트웨이에 상시 등록하면 도구 스키마 7종이 매 프롬프트에
실린다 — 이 구현은 MCP 를 프로토콜로만 소비해 그 비용을 없애면서 외부효과 승인 불변식을 그대로 지킨다.

## 사용 시나리오

1. **저장(happy path)**: Plaud 로 녹음 → 클라우드 전사 완료 → 워처 틱(10분, Plaud 폴은 내부 30분
   간격)이 신규 녹음을 발견해 노트 본문을 `~/.hermes/plaud-sync/notes/` 에 동결하고, `#agent-chat`
   요청별 스레드에 승인 카드(녹음 id·시각·대상 경로·action_hash)를 게시한다. cha 가 ✅ 하면 다음 틱이
   게이트 레코드를 전사하고 vault `000_PARA/Area/Lifelog/<연도>/` 에 push, 같은 스레드에
   "✅ lifelog 저장 완료"가 달린다. 이후 recall 로 검색된다.
2. **거부**: ⛔ 를 누르면 레코드는 abandoned 로 종결되고 노트는 push 되지 않는다(스레드에 취소 통지).
3. **fail-closed**: 동결 본문이 없거나 sha 가 어긋나면 게시도 push 도 하지 않는다. 승인 뒤 본문이
   바뀌면 obsidian_write 게이트의 경로·제목·본문 바인딩이 push 를 거부한다.

## 롤아웃 (1회, 소유자)

워크스테이션에서 `npm install -g @plaud-ai/cli` 후 `plaud login`(브라우저 OAuth — 토큰은
`~/.plaud/tokens.json`) → `~/.plaud` 를 노드 agent 계정 홈으로 복사 → `automation/plaud_sync/deploy.sh`
→ 첫 틱 후 승인 카드 확인. **CLI 는 로그인·수동 조회용 사람 인터페이스**(chalk 출력, `--json` 없음)이고
워처는 계속 `@plaud-ai/mcp` stdio JSON-RPC 를 쓴다. 두 패키지는 같은 `~/.plaud` 디렉터리를 쓰되
**파일이 다르다** — CLI 는 `tokens.json`, MCP 는 `tokens-mcp.json` 을 읽으므로 로그인 뒤 `tokens.json`
을 `tokens-mcp.json` 으로 한 번 복사해 씨앗을 심는다(각자 refresh_token 으로 이후 자체 갱신). SSH 로
브라우저가 안 뜨면 로그인이 출력한 `web.plaud.ai/platform/oauth?...` URL 을 브라우저에서 직접 열거나
콜백 포트(`localhost:8199`)를 `ssh -L 8199:localhost:8199` 로 포워딩한다. `@plaud-ai/mcp install` 은
헤드리스에서 로그인에 도달하지 못하고 감지된 클라이언트에 MCP 설정을 써버리므로 쓰지 않는다. 실데이터
스키마(get_note=top-level 리스트·항목 `data_content`, get_transcript=`segments`+`next_cursor`, 빈 녹음=`[]`)
는 2026-09-02 로그인 후 측정해 fetch.py 를 그에 맞춰 교정했다.

## 관련

- 선택 기능: [전문 첫 저장 전 교정](plaud-전사-동결전-교정.md).
  노드 명령을 명시한 경우에만 동결 전에 전문을 교정하며, 원본 전사 파일은 보존한다.
  설정이 없으면 기존 동작·노트 바이트가 그대로다.

- 구현: `automation/plaud_sync/`(mcp_client·note·model·store·binding·render·approval_gate·watch_step·
  sync·fetch·effects_live·lifelog_model·lifelog_fields·lifelog_extract·lifelog_extract_live·
  cron/plaud_sync_watch.py) · 계획 `.omo/plans/plaud-lifelog-sync.md` · v2 양식 `.omo/plans/plaud-lifelog-format-v2.md`
- 승인: `ApprovalKind.OBSIDIAN_WRITE` 재사용(memory_relocate producer 패턴), conformance 등록
  (`tests/unit/approval_conformance_inventory.py`)
- 검증: `tests/unit/test_plaud_sync_*.py` · `test_plaud_mcp_client.py`
- 경계: lifelog 는 회의록 체인을 자동 호출하지 않는다 — 과제 회의 녹음은 소유자 명시 지시로만
  meeting ingest 로 보낸다.

## 노트에 Plaud 포스터 이미지를 싣지 않는다 (2026-09-04 실측)

Plaud 요약(`get_note` 의 `data_content`)은 `![PLAUD NOTE](permanent/<uid>/<file>/summary_poster/card_*.png)`
로 시작한다 — Plaud 앱이 요약 카드 그림을 자기 스토리지에 두는 **상대 키**라 vault 에는 존재할 수
없고, Obsidian 은 상대 경로를 vault 안에서 찾아 "…png 을 찾지 못했습니다" 를 띄웠다. 승인 카드
미리보기는 이미 이미지를 건너뛰었지만(`render.py`) 노트 본문은 요약을 그대로 실었다. 이제
`note.render_lifelog_body` 가 **절대 URL(`http(s)://`)이 아닌 이미지 마크다운을 요약에서 제거**하고
그 줄만 있던 행은 지운다(`lifelog_fields.strip_unresolvable_images` — v2 양식에서 옮겨졌다). 이미 push 된 노트는 소급 수정되지 않는다 —
그 줄은 vault 에서 손으로 지우면 된다(같은 녹음의 재동기화는 processed 원장이 막는다).

## 승인 카드 미리보기 · 카드 재게시 · 상태 조회 스킬 (2026-09-02 소유자 요청)

**카드에 내용이 보인다(v2).** 승인 카드가 동결된 노트 본문의 `## 요약` 상위 5줄(문장 단위,
줄당 160자, 요약이 비어 있으면 `## 전문`)을 인용한다 — id 와 해시만 보고 ✅ 를 누를 수는
없다는 지적에서 왔다. 미리보기는 표현일 뿐이라 승인 해시(`action_hash`)는 바뀌지 않는다.

**이미 올라간 카드를 새 형식으로 바꾸기.** 승인 파사드는 *내용*이 바뀔 때만 재게시하므로
형식 변경은 스스로 반영되지 않는다. agent 계정에서 한 번:

```bash
python3 ~/.hermes/scripts/plaud_sync_watch.py --repost-posted
```

순서는 정상 틱(이미 눌린 ✅ 를 먼저 소비) → `posted` 카드 삭제·`planned` 복귀 → 틱(새 카드
게시)이며 같은 lock 아래에서 돈다. 새 카드는 **같은 요청 스레드**에 올라간다 — 요청별
스레드 API 는 조회 없이 매번 새로 만들기 때문에, 레코드가 기억하는 `approval_thread_id` 를
재사용 후보로 넣는다. 삭제에 실패한 카드는 건드리지 않는다(상태와 카드가 어긋나지 않게).

**"plaud 상태" 한마디로 조회.** governed 스킬 `skills/plaud` 가 `~/.hermes/plaud-sync/state.json`
을 읽어 마지막 폴 시각(KST 병기)·status 별 건수·승인 대기 목록(녹음 id·스레드 id·파일명)을
보고한다. 읽기 전용이고 stdlib 만 쓴다. Discord 에서 "plaud 상태 알려줘" / "plaud 승인 대기
몇 건이야" 라고 하면 에이전트가 이 스킬을 골라 바로 실행한다. 직접 돌릴 때:

```bash
python3 /srv/autophagy-skills/live/plaud/scripts/plaud_cli.py status [--json]
```

왜 스킬인가 — 대안과 비교: 에이전트 메모리에 명령을 적어 두는 것은 회상이 불안정하고,
소유자가 경로를 외우는 것은 이 요청이 피하려는 바로 그것이며, 주기 통지에 상태를 싣는 것은
묻지 않은 때에 소음이 된다. SKILL.md 는 트리거가 맞을 때만 로드되므로 상시 컨텍스트 비용이
없고, 배포는 릴리스 ✅ 하나로 다른 스킬과 함께 마운트된다.

**저장이 실패하면 숨기지 않는다(2026-09-02).** ✅ 뒤 vault 저장(`write_note`)이 실패하면 레코드는
`approved` 에 남아 다음 틱에 재시도하되, 사유가 `last_block_reason` 에 적히고 워처 stderr 에
`plaud-sync write error: …` 로 남는다. `plaud 상태` 가 `저장 대기(approved) N건: <id> · 사유 …` 로
보여 주므로 "✅ 했는데 결과가 없다" 는 상태를 한 줄로 설명할 수 있다. 저장이 성공하면 사유는
지워진다. 실측 원인은 obsidian-write 클론의 fetch 타임아웃이었다(후속 과제 참조).

**결과 통지를 봉투로 보낸다(2026-09-11).** 저장 완료·취소 결과는 기존 사실과 녹음 id·대상
식별자를 소유자 메시지 봉투에 담아 승인 카드가 있는 스레드에 보낸다. 스레드 전송이 실패해도
폴백은 같은 스레드이므로 두 경로 모두 자기 링크가 없다. 옛 런타임에는 기존 문자열을 그대로
보내며, 노트 본문·전문은 봉투가 아니라 카드 미리보기와 vault에 남긴다. 이미 게시된 카드와
승인 해시는 바꾸지 않는다. 진행 중 ACK는 종결로 만들지 않으며 아카이브 규칙도 그대로다.

**결과를 요청 스레드에 닫는다(2026-09-03).** 저장 완료·취소·만료 같은 종결 결과는 이제
`origin_notice.deliver(outcome=)` 로 나가므로, 요청을 올린 그 승인 스레드에 게시되고 이름에
상태 접두어(✅ 완료·⛔ 취소·⌛ 만료)가 붙은 채 아카이브된다 — 끝난 요청이 진행 중 보드에 남지
않는다. 리액션 transport 와 ✅→게이트 기록 전사는 `automation/interop/reaction_approval.py`
공용 모듈로 옮겼다. plaud_sync 와 memory_relocate 가 같은 코드를 각자 들고 있던 이유는
memory_relocate 를 import 하면 memory_curator 사슬이 통째로 딸려오기 때문이었는데, 새 모듈이
그 셋 중 어느 것도 import 하지 않아 사유가 사라졌다. 같은 날, 녹음명이 일시로 시작하면 노트
파일명에 날짜가 두 번 들어가던 것(`2026-09-02-2026-09-02-13…`)도 슬러그 앞 날짜 토큰만 떼어
고쳤다. 시간 접미와 `recording` 폴백은 그대로다.

**녹음 오디오를 노드에서 직접 전사한다(2026-09-04).** 발견된 녹음은 `planned` 앞의 `transcribing` 에 놓이고, watch.lock 을 푼 뒤 `transcribe_live` 가 오디오를 내려받아 speechtotext CLI(whisper.cpp + 화자 분리, `SPEECHTOTEXT_BACKEND=local` 고정·Drive 발행 0)로 전사한 전사본을 `~/.hermes/plaud-sync/transcripts/<노트 stem>.md` 에 남기고 노트 `## 전문` 을 그것으로 채운다. 회의록이 필요하면 소유자 지시로 `meeting_cli.py ingest --file <그 경로>`. 상세: [Plaud 녹음 로컬 전사](plaud-녹음-로컬-전사.md).

**✅ 를 읽은 틱에서 바로 저장한다(2026-09-03).** 예전에는 ✅ 를 읽어 `approved` 로 바꾸는 틱과 vault 에 쓰는 틱이 달라 저장까지 최대 20분(두 틱)이 걸렸다. 이제 같은 틱에서 곧바로 쓰고 통지하므로 ✅ 뒤 지연은 최대 한 틱(10분)이다. 저장이 실패하면 예전처럼 `approved` 로 남아 다음 틱에 재시도한다.

**노트 v2 양식과 사람·장소·결정·할 일 추출(2026-09-04, B안).** 노트가 소유자 Obsidian Linter 가 그대로 두는 frontmatter 로 시작하고 `## 한눈에` Dataview 필드·`## 결정 · 할 일` 체크리스트·접힌 `## 전문` 을 갖는다. 사람·장소·결정·할 일은 glm-main 이 **로컬 전사가 들어온 finalize** 에서 뽑고(클라우드 초안은 LLM 미호출), 특허 민감 텍스트는 LLM 에 닿지 않으며 키가 없으면 `- 추출:: 생략 (LLM 미설정)` 으로 적힌다. 승인 카드 v3 는 한눈에 줄을 먼저 인용한다. 상세: [Plaud lifelog 노트 v2 양식](plaud-lifelog-노트-v2-양식.md).
