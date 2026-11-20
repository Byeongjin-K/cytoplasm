# Plaud 녹음 로컬 전사 (plaud_sync `transcribing` 스테이지)

## 무엇을

Plaud 녹음의 **오디오 파일을 내려받아 노드에서 직접 전사**한다 — speechtotext 스킬이 이미 가진
whisper.cpp + sherpa-onnx 화자 분리를 그대로 써서, 결과 전사본(.md: `- 화자:` 범례 + `---` +
`[HH:MM:SS] 화자N · 이름` 블록, 한 줄 한 문장)을 `~/.hermes/plaud-sync/transcripts/<노트 stem>.md` 에
남기고, lifelog 노트의 `## 전문` 을 그 전사로 채운다. Plaud 클라우드 전사는 로컬 전사가 끝내 실패할
때의 폴백일 뿐이다.

## 왜

- 소유자 지시(2026-09-04): "recording 된 audio 파일을 내가 직접 전사해서, 화자 구분 후 텍스트로
  저장 — 그 저장물을 life log 나 회의록 파이프라인(추후 기능 포함)이 쓰게".
- Plaud 클라우드 전사는 `speaker_1` 같은 라벨만 있고 우리 용어집·자기소개 이름 규칙·문장 단위 출력이
  없다. 그리고 **파일이 노드에 남아야** `meeting_cli.py ingest --file` 같은 다른 파이프라인이 읽는다.
- 음성은 노드 밖으로 나가지 않는다. 자식 env 에 `SPEECHTOTEXT_BACKEND=local` 을 고정하므로 도구가
  없으면 exit 4 로 멈추고 API 로 폴백하지 않으며, `DRIVE_PUBLISH_ENABLED=0` 으로 개인 녹음이 Drive
  `전사본/` 폴더에 올라가지 않는다 — 승인된 Obsidian 노트가 유일한 목적지다.

## 시각 정규화

로컬 전사에서만 본문 시각이 제목·경로보다 9시간 이르던 결함을 고쳤다. 증명된 결함은 `get_file`의 오프셋 없는 UTC 문자열을 현지 시각으로 읽은 것이다 — 회귀 테스트는 명시적 UTC 오프셋을 단 발견 기준 입력과 오프셋 없는 `get_file` 입력을 대조하며, 모든 `list_files` 응답이 오프셋을 단다고 주장하지는 않는다. 실측 스모크에서는 합성 09:30 UTC 가 제목·frontmatter·한눈에·출처 전부 18:30 Asia/Seoul 로 찍히고 전사 본문은 그대로였다. 이제 `get_file` 경계에서 오프셋 없는 `created_at`·`start_at`을 `+00:00`으로 정규화하고, 출처 줄도 제목·frontmatter·`녹음::`과 같은 현지 시각을 쓴다. 이미 vault에 작성된 세 노트의 보정은 소유자 결정 전까지 범위 밖이다.

## 어떻게 (틱 한 번)

1. **discovery**(30분 게이트) — 최소 길이(기본 5초) 미만은 건너뛰고, 나머지 새 녹음을 `planned` 대신 `transcribing` 으로 동결한다(초안 = Plaud 요약 +
   클라우드 전사, hash 는 finalize 때 다시 계산). **Plaud 가 요약도 전사도 만들지 않은 녹음도 동결한다** — 그런
   녹음이 이 경로가 있는 이유다(2026-09-05 까지는 빈 노트 가드가 `initial_status` 앞에 있어 매 폴 skip 됐고, 64분
   녹음이 로컬 전사에 닿지 못했다).
2. **resolve** — 카드 게시·✅ 판독·vault 저장(watch.lock 아래, 기존 그대로). `transcribing` 은 건드리지 않는다.
3. **watch.lock 을 푼다** → 전사 스텝(`transcribe_live.run_transcribe_step`, 틱당 1건 — **시도 횟수가 적은 녹음부터**, 같으면 예전 순):
   보류 건의 클라우드 재확인(전사 슬롯과 별도, 잠금이 바빠도 수행) →
   `pipeline_lock`(speechtotext 워처와 공유 — whisper 는 같은 자원, 못 잡으면 로컬 전사 양보) →
   `get_file` presigned URL(24h, JSON 뒤 산문이 붙어 `raw_decode`) → 스트리밍 다운로드(상한 1 GiB,
   `audio/<id>.<presigned URL 의 확장자>` — 2026-09-15 15:31 UTC 부터 Plaud 가 `.ogg` 를 준다, 그 전은 `.mp3`, 캐시) → speechtotext CLI `transcribe --file … --label <stem>` → 전사본을
   `transcripts/<stem>.md` 에 저장 → 요약을 `get_note` 로 갱신하고 노트 본문을 재조립 →
   **commit: watch.lock 을 blocking 으로 다시 잡고 레코드가 여전히 `transcribing`·같은 action_hash 인지
   재검사한 뒤 한 번 저장** → 오디오 삭제 → `planned`.
4. 승격된 건이 있으면 같은 틱에서 resolve 를 한 번 더 돌려 카드를 바로 올린다(다음 틱 10분을 기다리지 않는다).

117분 녹음은 노드에서 약 45분 걸린다(0.4× 실시간 추정; 2026-09-05 실측은 CUDA 노드에서 64분 → 약 6분, 0.1×). 그동안 watch.lock 이 비어 있으므로 다음 틱들은 ✅ 를
정상 소비하고, 전사 스텝만 pipeline_lock 에 막혀 양보한다.

## 실패 분류

| 종류 | 예 | 처리 |
|---|---|---|
| 환경(노드) | rc=3(governed 거부)·rc=4(whisper/sherpa 없음), CLI 미마운트, MCP 오류, 네트워크 | **카운트 안 함**, 사유를 `last_block_reason` 에 적고 매 틱 재시도 |
| 녹음 | rc≠0(빈 전사·잘림·미지원 형식), 시간 초과, 오디오 상한 초과 | `transcribe_attempts`+1; 상한(기본 2) 도달 시 **클라우드 전사로 폴백**해 `planned`, 노트 출처 줄에 `PLAUD 클라우드 전사(로컬 전사 N회 실패: …)`; 클라우드도 요약·전사가 없으면 `planned` 로 올리지 않고 `transcribing` 에 보류한 뒤 1·2·4·8·16·24시간 상한 백오프로 로컬 재시도한다. 클라우드는 매 틱 확인하고, 총 실패 상한(기본 5)에도 비어 있으면 `abandoned`로 닫는다. 대기 건은 전사 슬롯을 쓰지 않는다 |
| stale | commit 시점에 레코드가 바뀌어 있음 | 아무것도 덮어쓰지 않는다 |

**잡 상한 안에서 끝내기(2026-11-20)**: 워처는 정기 작업 상한(3600초) 안에서 돈다. 전사 CLI 상한은 그보다 길어서(기본
21600초) 23시간짜리 녹음이 상한에 걸리면 CLI 가 아니라 작업 전체가 끊겼다 — 시간 초과가 시도로 세어지지 않아 그 녹음이
매 틱 대기열 맨 앞(시도 0·가장 오래됨)에 남고, 그 뒤 녹음은 전사를 시작하지 못했다. 이제 워처가 작업 시작 시각+상한을
전사 단계에 마감으로 넘기고, CLI 는 그 마감에서 뒤처리 몫(600초: 요약·필드 추출·교정·commit)을 뺀 시간 안에 끝난다.
넘기면 CLI 와 그 아래 whisper·화자 분리 프로세스를 프로세스 묶음째 멈추고 **녹음 실패로 센다**(위 표의 시간 초과).
세어진 녹음은 대기열에서 시도 0 녹음 뒤로 가므로 다음 틱은 뒤 녹음부터 전사한다. whisper 창은 speechtotext 창 캐시에
남아 다음 시도가 이어서 하고, 상한(기본 2회)에 닿으면 클라우드 전사로 폴백한다. 남은 시간이 60초도 안 되면 CLI 를
시작하지 않고 세지 않은 채 다음 틱으로 넘긴다. 마감이 없는 실행(손으로 돌린 `run_transcribe_step`)은 예전처럼
`PLAUD_SYNC_TRANSCRIBE_TIMEOUT` 만 따른다.

**끝난 전사본은 다시 만들지 않는다(2026-11-20)**: 같은 날 23시간 녹음은 전사를 끝낸 뒤 필드 추출이 모델 시간 초과로
막혔고, 추출 실패는 세지 않고 다음 틱에 다시 하는 환경 실패라 다음 틱이 녹음 전체를 처음부터 다시 전사했다(창 캐시는
성공한 전사 뒤에 비워진다). 이제 CLI 가 끝낸 전사본은 `transcripts/.kept/<노트 이름>.json` 이 가리키고, 레코드가
`transcribing` 을 떠날 때(`planned`·폴백·폐기 commit)까지 다음 시도가 CLI 없이 그 전사본을 쓴다
(stderr `PLAUD-TRANSCRIPT-REUSED`). 가리키는 파일만 지우고 전사본 파일은 지우지 않는다.

**2026-09-16 · `.ogg` 사고**: Plaud `get_file` 이 전날 15:31 UTC 부터 presigned URL 을 `.ogg` 로 주기 시작했고
(그 전 녹음은 전부 `.mp3`), speechtotext 의 형식 게이트 `stt_audio.SUPPORTED_SUFFIXES` 에 `.ogg` 가 없어
**ffmpeg 가 돌기도 전에** rc=5「지원하지 않는 형식입니다」로 거부됐다 — 녹음 실패로 세어져 3건이 `transcribing`
보류(백오프), 2건은 5회 상한에서 `abandoned`. 저장소는 바뀐 것이 없었고 공급자가 형식을 바꾼 것이다. 조치는
게이트에 `.ogg` 를 더한 것 하나(speechtotext 1.3.1) — 로컬 경로는 어떤 형식이든 ffmpeg 로 16 kHz wav 를 만들므로
워처에서 따로 변환할 이유가 없다(두 번째 형식 정책이 생긴다). 릴리스 수렴 뒤 보류 3건은 각자의
`next_transcribe_at` 에 스스로 재시도하고(캐시된 오디오를 다시 받지 않는다), `abandoned` 2건은 소유자가
`plaud_sync_watch.py --reprocess <녹음 id>` 로 되살린다. 진단상 주의: 보류 중 클라우드 재확인이 일시적
`get_note: PlaudMcpError` 를 내면 그것이 `last_block_reason` 을 덮어써 원래 rc=5 사유가 `plaud 상태` 에서 사라진다
([후속 과제](../follow-ups.md#plaud-ogg-형식-사고-착지-후-남긴-것-2026-09-16)).

## 사용 시나리오

1. **happy**: Plaud 로 녹음 → 30분 내 발견(`transcribing`) → 다음 틱에 전사 → 카드 미리보기가 로컬 전사 상위
   5줄 → ✅ → vault `000_PARA/Area/Lifelog/<연도>/` 에 `## 요약` + `## 전문`(화자 범례·시각 블록) + 출처 줄
   `… · 전사: 로컬 전사 local:ggml-large-v3-turbo-q5_0 · 화자 분리`. 진행은 "plaud 상태" 한마디로 본다.
2. **회의록으로**: 소유자가 "이 plaud 녹음 회의록으로 만들어" → 에이전트가 `plaud_cli.py status --json` 의
   `transcripts[].path` 를 찾아 `meeting_cli.py ingest --file <path> --label … [--project …]`. lifelog 가 회의록
   체인을 **자동 호출하는 일은 없다**(기존 경계 유지).
3. **노드에 도구가 없다**: `전사 대기(transcribing) 1건 · 시도 0 · 사유 rc=4 로컬 전사 도구를 찾지 못했습니다`
   가 매 틱 그대로다. 노드 `~/.env.secrets` 의 `SPEECHTOTEXT_WHISPER_BIN/MODEL`·`SPEECHTOTEXT_DIARIZE_*` 를 고치면
   다음 틱에 진행한다. 당장 옛 동작(클라우드 전사로 바로 카드)이 필요하면 `PLAUD_SYNC_TRANSCRIBE=0`.

현재 정책 상세는 [전사 정책 게이트](plaud-전사-정책-게이트.md)를 따른다. Plaud 요약이 없으면 같은 필드 추출 호출에서
얻은 요약으로 채운다. 아래 실측은 정책 적용 전의 역사이며 2초 녹음·빈 요약 사례는 현재 기본 동작이 아니다.

## 실측 (2026-09-04)

- `get_file` 은 `{id,name,created_at,start_at,duration,presigned_url,source_list,note_list}` + 산문 꼬리. presigned
  URL 은 GET 만 서명돼 HEAD 가 403 → 크기 상한은 `Content-Length` 와 스트림 누계로 건다.
- 워크스테이션 드라이버(실 MCP·실 S3·CLI 는 `SPEECHTOTEXT_CLI` 가짜): 2초 녹음 11,888 B(ID3 헤더) 다운로드 →
  `transcribing 1 → planned 1`, `transcripts/<stem>.md` 생성, `audio/` 비움, body sha 일치. whisper 실행 자체는
  노드 릴리스 뒤 첫 `transcribing` 레코드에서 확인한다(워크스테이션에 whisper 가 없다).

## 실측 (2026-09-05, 노드 첫 로컬 전사)

- 대상: 9/4 사고 녹음(Plaud 표기 64분 6초, mp3 실측 61분 10초 · 56 MiB — S3 Content-Range 총량과 바이트 일치, 다운로드 완전).
  9/4 에는 UTF-8 결함으로 2회 실패 → 빈 노트가 vault 에 쓰였고, 9/5 재발견은 "요약도 전사도 없다"로 매 폴 skip 됐다(위 수리).
- 레코드를 `transcribing` 으로 되돌린 뒤 틱 1회: whisper-cli(CUDA, large-v3-turbo-q5_0) 약 6분 → `transcripts/<stem>.md`
  46 KB · 문장 1,138 · 화자 블록 529 · 격리 구간 0 · 커버리지 64.7%(나머지는 침묵) → 노트 649 B → 52 KB(한눈에 사람·장소,
  결정 · 할 일 추출 성공, `## 요약` 은 Plaud 요약이 없어 `(요약 없음)`, 출처 `로컬 전사 local:ggml-large-v3-turbo-q5_0 · 화자 분리`)
  → 같은 틱에서 카드 `posted`. 오디오 캐시와 구간 캐시는 성공 후 비워졌다.
- 화자 분리는 화자1~41 이 전부 `미상`(과분할) — 전사 자체는 막지 않지만 범례가 읽기 어렵다(후속 과제).

## 관련

- 코드: `automation/plaud_sync/audio.py`(URL·다운로드) · `transcribe.py`(순수 스텝·실패 분류·폴백) ·
  `transcribe_live.py`(MCP·S3·subprocess·잠금·commit) · `cron/plaud_sync_watch.py`(lock 해제 순서) ·
  `model.py`(`transcribing`, `transcribe_attempts`, `next_transcribe_at`) · `skills/plaud`(status 1.1.1)
- 테스트: `tests/unit/test_plaud_sync_{audio,transcribe,transcribe_live,transcribing_status,policy_live,watch_transcribe}.py`
- env(노드의 기존 환경 설정 경로): `PLAUD_SYNC_TRANSCRIBE`(1) · `PLAUD_SYNC_TRANSCRIBE_PER_TICK`(1) ·
  `PLAUD_SYNC_TRANSCRIBE_ATTEMPTS`(2) · `PLAUD_SYNC_TRANSCRIBE_GIVE_UP`(5) · `PLAUD_SYNC_MIN_DURATION_MS`(5000) ·
  `PLAUD_SYNC_TRANSCRIBE_TIMEOUT`(21600, 워처에서는 잡 상한−600초로 깎임) · `PLAUD_SYNC_MAX_AUDIO_BYTES`(1 GiB);
  CLI 경로 주입은 `SPEECHTOTEXT_CLI` / `SPEECHTOTEXT_SCRIPTS`
- 규약: `automation/pipeline_lock.py`(규약 (n) — plaud 도 같은 lock), [plaud lifelog 동기화](plaud-lifelog-동기화.md),
  [음성 녹취 → 전사본 → 회의록](음성-녹취-회의록-자동화.md)
