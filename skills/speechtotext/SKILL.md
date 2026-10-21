---
name: speechtotext
description: "음성 녹음을 글로 옮기거나 녹음으로 회의록을 만들라고 할 때 사용. Google Drive 폴더에 올려둔 음성 녹취를 전사본(.md)으로 만들고, 그 전사본을 meeting 스킬로 넘겨 회의록까지 잇는 스킬. 전사는 기본이 로컬(whisper.cpp)이고, 2시간이 넘는 단일 녹취도 누락 검증을 통과해야만 회의록으로 넘어간다."
version: 1.8.0
author: autophagy-agents
---

# speechtotext — 음성 → 전사본(.md) → 회의록

변경 명령은 `/srv/autophagy-skills/live/speechtotext/scripts/`에서만 실행하며, 낡은 사본은 `STALE-SKILL-COPY-BLOCK`으로 거부한다.

소유자가 **감시 폴더에 녹취 파일을 놓으면** 5분 틱 워처가 그것을 집어 전사본을 만들고
곧바로 `meeting` 스킬로 넘겨 회의록·칸반·마일스톤까지 잇는다. 폴더에 파일을 놓는 행위가
`!meeting` 토큰과 같은 **명시 지시**이며, 그래서 별도 승인 게이트를 두지 않는다.

## 동작 방식 (에이전트가 지켜야 할 규칙)

1. **전사본 원문을 내 컨텍스트/응답에 붙여넣지 마라.** 회의 내용은 CLI 안에서만 흐르고
   결과는 JSON 요약(전사본 경로·글자수·커버리지·meeting 종료코드)으로만 읽는다.
2. 소유자가 **로컬 음성 파일 경로**를 대며 회의록을 요청하면 아래를 실행한다:

   ```bash
   python3 /srv/autophagy-skills/live/speechtotext/scripts/speechtotext_cli.py ingest \
     --file <음성경로> --label "<회의 라벨>"
   ```

   채널 지시라면 `--notify-channel <채널ID> --notify-message-id <지시 메시지ID>`를 함께
   넘긴다(그대로 meeting 에 전달되어 결과가 원 채널 스레드로 돌아간다).
3. 전사본만 필요하면 `transcribe`, 회의록까지면 `ingest`, 이미 있는 전사본을 다시 다듬기만
   하려면 `polish` 다. `transcribe` 와 `polish` 는 meeting 을 호출하지 않는다.
4. **거부는 그대로 전달하라.** 종료코드가 곧 사유다 — 3=크기 초과, 4=로컬 전사 도구 부재,
   5=미지원 형식/빈 전사, 6=전사 API 실패, 7=회의록 체인 실패(전사본은 남아 있으므로
   재시도해도 전사를 다시 지불하지 않는다), 8=**누락 의심**. 내용을 추측해 채우지 마라.
   허용 형식은 `stt_audio.SUPPORTED_SUFFIXES` 하나가 정한다 — flac/mp3/mp4/mpeg/mpga/m4a/**ogg**/wav/webm
   (ogg 는 2026-09-16, Plaud `get_file` 이 전날부터 .ogg 를 주기 시작해 로컬 전사가 ffmpeg 앞에서
   rc=5 로 막힌 뒤 더했다 — 로컬 경로는 어떤 형식이든 ffmpeg 로 16 kHz wav 를 만들므로 이 게이트가
   유일한 장벽이었다). Drive 감시 폴더의 `is_audio` 도 같은 집합을 읽는다.

## 전사 백엔드 — 로컬이 기본인 이유

외부 전사 API를 쓰면 회의 **원음**이 노드 밖 공유 프로바이더로 나간다. 그래서 로컬
whisper.cpp 가 해석되면 그것을 쓴다.

| `SPEECHTOTEXT_BACKEND` | 동작 |
|---|---|
| `auto`(기본) | 로컬 도구가 해석되면 로컬, 아니면 API |
| `local` | 로컬만. 도구가 없으면 **exit 4 로 중단하고 절대 네트워크로 폴백하지 않는다** |
| `api` | OpenAI 호환 `/v1/audio/transcriptions` (`SPEECHTOTEXT_BASE_URL` 로 대체 가능) |

## 2시간이 넘는 단일 녹취

- **로컬**: 기본 15분 창 + 15초 겹침으로 구간마다 whisper.cpp를 실행한다. 이음매는 다음
  창이 소유하며, 실패한 창만 격리하고 나머지는 보존한다. 25MiB 상한은 로컬에는 없다.
- **API**: 25MiB를 넘으면 15분 창 + 10초 겹침으로 나눠 올리고, **겹친 구간에서 실제로
  반복되는 텍스트만** 지운 뒤 순서대로 잇는다(일치하지 않으면 양쪽을 모두 남긴다 —
  중복은 불편이지만 누락은 실패다).
- **누락 검증**: whisper.cpp full JSON 의 구간 타임스탬프 합집합을 `ffprobe` 실제 길이와
  대조한다. 침묵은 결함이 아니므로 커버리지 비율만으로 판정하지 않고, 설명되지 않는
  앞/뒤 구간이나 보통의 쉼보다 훨씬 긴 내부 공백이 있으면 **exit 8 로 거부**한다. 길이를
  알 수 없으면 완결을 주장하지 않는다(`COVERAGE-UNKNOWN`).
- **반복 붕괴 검사**: 커버리지는 "구간이 비었는가"만 본다. 디코더가 무너지면 타임스탬프는
  그대로 채워진 채 같은 문장만 되풀이되므로 커버리지는 통과한다. 그래서 최다 8어절의
  점유율을 따로 잰다(`SPEECHTOTEXT_MAX_REPEAT`, 기본 8%). 여러 창 중 반복이 의심되는 창은
  기존 실패 표식 아래 `<details><summary>⚠ 반복 의심 구간 … (repetition=…) — 접힘</summary>`으로
  문장 원문을 보존한다. 펼치면 읽을 수 있고, 재다듬기·화자 처리도 내부 줄을 바꾸지 않는다.
  meeting 추출 입력은 접힌 본문을 제외하지만 부록 원문에는 그대로 남긴다. JSON 파싱 불가 등은
  표식만 남으며, `SPEECHTOTEXT_ALLOW_INCOMPLETE=1`인 라이프로그는 이전처럼 접지 않는다.
  단일 창·접힌 본문 밖의 전체 반복 검사는 기존 exit 8 거부를 유지한다.
  실측: 정상 구간 1.2% vs 붕괴한 94분 녹취 57.1%.
- **문맥 이월은 기본으로 끈다(`-mc 0`)**. 원래는 "문맥을 자르면 연속성이 준다"는 이유로 쓰지
  않았는데, 실제 94분 한국어 녹취가 그 가정을 뒤집었다 — 이월을 켜면 디코더가 자기 출력을
  되먹어 전사본의 28%가 한 문장 910회 반복이 됐고, 같은 구간을 `-mc 0` 으로 다시 디코딩하니
  정상 수준(1.2%)으로 돌아오며 사라졌던 28분치 발화가 복구됐다. 되돌리려면
  `SPEECHTOTEXT_WHISPER_CONTEXT=-1`.
- `-nf`는 쓰지 않는다(실패한 창을 구제하는 온도 폴백을 끈다). `--vad`는 기본으로 끄되
  `SPEECHTOTEXT_VAD_MODEL`을 명시하면 켤 수 있다(조용한 발화가 잘릴 수 있음).
- **창 캐시는 인식 조건까지 묶는다.** 오디오 SHA·모델·실행 파일 지문·창 계획뿐 아니라
  언어·실제 prompt·문맥 길이·디코딩 플래그·DTW·부분 전사 허용 여부가 키에 들어간다.
  조건을 바꾸면 옛 창을 재사용하지 않고, 같은 조건으로 재시도하면 성공 창부터 이어 간다.
- **`--dtw`는 옵트인**이다. `SPEECHTOTEXT_WHISPER_DTW`에 모델 프리셋(예: `large-v3-turbo`)을
  명시한 경우에만 붙인다. 미설정이면 기존 토큰 시각을 쓰며, DTW를 자동으로 켜지 않는다.

## 전사본 다듬기 (정리)

전사기는 모든 구간을 **한 줄**로 이어 붙인다 — 실제 94분 녹취가 38,216자 한 줄로 나왔다.
충실한 전사본이지만 읽을 수 없는 문서다. 그래서 `.md` 로 쓰기 직전에 한 번 다듬는다:

- 문장 경계로 나누고 블록으로 묶는다(아래 「문장 단위 출력」). 화자가 없으면 4문장·180자를 모두
  넘겨야 블록이 닫힌다.
- **연속으로 완전히 같은 문장**만 하나로 접는다. 나중에 다시 나오는 같은 말은 사람이 다시
  한 것이므로 남긴다 — 이 스킬의 반복 원칙 그대로, 중복은 불편이고 누락은 실패다.
- **낱말은 들린 그대로 둔다.** 전사본은 증거라서 용어 교정을 새기지 않는다 — 교정은 이
  전사본으로 회의록·라이프로그 노트를 만들 때 그 문서에 걸린다(아래 「용어집」).
- **요약하지 않는다.** 결정사항·액션아이템·마일스톤을 읽어내는 일은 meeting 의 몫이고
  이 경계를 넘지 않는다.

## 문장 단위 출력

전사본 본문은 **블록**의 나열이고, 블록은 빈 줄 하나로 갈린다. 블록 안에서는 **한 줄에 한 문장**만
쓴다. 블록의 첫 줄은 헤더이며 `[HH:MM:SS] 화자N · 이름` 형식이다. 시각만 알면 `[HH:MM:SS]`,
화자만 알면 `[--:--:--] 화자N`, 둘 다 모르면 헤더 없이 문장 줄만 남는다(레거시 문단을 다시 다듬으면
이 모양이 된다).

```
[00:03:12] 화자1 · 김민수
안녕하세요, 저는 김민수라고 합니다.
오늘은 해양 계측 일정부터 보겠습니다.

[00:03:41] 화자2 · 이영희
계측기 납품이 2주 밀렸습니다.
```

**왜 바꿨나**: 94분 실측 녹취가 문단 140줄로 나왔고 가장 긴 줄이 1,137자였다. 충실하지만 소유자가
그 안에서 아무것도 찾지 못했다. 같은 녹취를 문장 줄로 쓰면 735줄이 되고, 각 블록이 언제 시작했는지도
줄 위에 적힌다. 사람이 읽는 단위가 문장이므로 문서의 줄도 문장이다.

타임스탬프는 whisper.cpp 가 이미 토큰마다 보고하던 값이다. 예전 경로는 구간을 한 문자열로 이어붙이며
그 값을 버렸다. 문법은 `stt_blocks.render()` 가 쓰고 `stt_blocks.parse()` 가 되읽으므로, 디스크에 이미
있는 옛 전사본과 새로 다듬은 전사본이 같은 문서 형태로 수렴한다(`polish` 는 멱등).

화자가 하나도 없으면 블록은 예전 문단 규칙(4문장·180자를 모두 넘겨야 닫힘)으로 끊고, 화자가 있으면
**같은 화자가 이어지는 최대 구간**이 한 블록이다.

### 문장 안 끼어듦 `[화자2: 네]` (1.6.0)

한 사람이 말하는 도중 다른 사람이 짧게 맞장구치면, 그 말은 문장을 자르지 않고 **그 문장 줄 안에**
표시된다. 이름을 알면 헤더처럼 `[화자2 · 이영희: 네]`로 적는다.

```
[00:03:12] 화자1 · 김민수
그 부분은 [화자2 · 이영희: 네] 다음 주까지 정리하겠습니다.
```

- 표식은 원래 낱말을 대괄호로 감쌀 뿐이다. 표식을 걷어 내면 말이 바이트 그대로 돌아오고
  (`stt_asides.extract`), `stt_blocks.parse()`가 되읽어 같은 문서로 수렴한다.
- 끼어듦이 되는 조건: 다른 화자의 turn 과 **직접 겹친** 낱말 근거가 있고(`reason=direct`,
  200ms 허용 오차만으로는 아니다) 길이가 1초 미만이다. 1초를 넘으면 화자 교대라 예전처럼 따로 적는다.
- 근거 없는 짧은 꼬리(2초 미만, 미상·겹침·허용 오차만)는 `화자0` 블록이 되지 않고 **원래 문장에
  다시 붙는다**(`stt_settle`). 길고 근거 없는 말은 여전히 `화자0`으로 따로 남는다 — 주인 없는 말을
  주인 있는 말로 만들지 않는다.
- 화자 수 질의(`stt_speaker_count.unlabelled`)와 평가 정답 수집(`capture_text`)은 표식의 라벨을
  말로 세지 않는다. 표식 없는 옛 전사본은 바이트 그대로 다시 렌더된다.

## 화자 구분

로컬 whisper.cpp 경로에서만 동작한다. 기본은 **sherpa-onnx**, 선택은
`SPEECHTOTEXT_DIARIZE_BACKEND=pyannote`와 격리 `stt-engines` CLI다. 추론은 노드 안에서 하며
원음을 외부 전사 API로 보내지 않는다. 관측된 turn의 첫 시각 순서로 `화자1`, `화자2` … 를 매긴다.

**단어 시각이 있으면 문장보다 먼저 화자를 배정한다.** BPE 조각을 낱말로 묶고 정수 밀리초
겹침·최소 지지·우세 차이로 판정한 뒤 같은 판정끼리 문장을 조립한다. 지지가 없을 때의 근접
허용은 200ms이며, 경계 동률·낮은 지지·시각 부재는 미상이다. **직전 화자를 상속하지 않는다.**
동시 발화는 참여자를 보존하는 겹침 판정이고, 미상과 겹침 모두 본문에는 `화자0`으로 표시한다.
`화자0`은 사람이 아니라 불확실성 표식이라 자기소개·LLM·소유자 이름 배정 대상에서 제외한다.
세그먼트 시각만 있는 옛 입력은 화자 경계·15초 분할 경로를 유지하되 근거 없는 상속은 미상으로 바꾼다.

- **fail-soft**: 도구·모델 부재나 실행 실패는 `DIARIZE-FAIL <사유>` 후 전사를 계속한다.
  분리 결과가 아예 없으면 화자 헤더 없이 계속한다. 배정이 실행됐으나 근거 없는 발화는
  `화자0`으로 남고, 누락 표식은 발화로 배정하지 않는다.
- **강제 정렬은 별도 옵트인**: `SPEECHTOTEXT_ALIGN_BACKEND=whisperx`면 격리 CLI의 문자 시각을
  원 토큰에 되돌린다. 기본 `none`은 정렬 subprocess를 만들지 않는다. 실패는 `ALIGN-FAIL` 후
  원 토큰 시각을 보존하며, 숫자·기호 등 정렬 불가 문자를 임의 보간하지 않는다.
  전사본 머리말의 `- 토큰 시각:`은 `offsets`·`dtw:<프리셋>`·`aligned:whisperx`를 구별하고,
  `- 화자 배정:`은 `word`·`legacy`를 구별한다(배정 미실행 시 그 줄은 없다).
- `--speaker-count N`: 화자 수를 알면 클러스터 수를 고정한다(`--clustering.num-clusters`).
- `--no-diarize`: 이번 실행만 화자 분리를 건너뛴다.
- `SPEECHTOTEXT_DIARIZE_THRESHOLD`(기본 `1.0`): 화자 수를 모를 때 쓰는 군집 임계값. **낮추면 화자를 더
  잘게 쪼개고(같은 사람이 둘로 갈릴 수 있다), 올리면 서로 다른 사람이 한 화자로 합쳐진다.**
  기본값이 `1.35`이던 동안 소유자가 화자 3명·4명으로 확인한 라이프로그 두 녹음이 **둘 다 화자
  1명**으로 나왔다(2026-09-07 노드 실측). 발화 시간 5% 이상만 화자로 센 실질 화자 수와 턴 수:

  | 임계값 | 272.5초 (정답 3인) | 549.4초 (정답 4인) |
  | --- | --- | --- |
  | 0.8 | 6명 / 43턴 | 7명 / 150턴 |
  | **1.0** | **4명 / 40턴** | **4명 / 139턴** |
  | 1.2 | 2명 / 30턴 | 2명 / 104턴 |
  | 1.35 (옛 기본값) | 1명 / 12턴 | 1명 / 51턴 |

  옛 `1.35`는 64분 2인 녹음에서 골랐지만 그 표는 `--min-duration-on/off`를 **주지 않고** 잰
  것이고 프로덕션은 그 가드와 함께 돈다 — 실행 조건과 다른 조건에서 고른 값이었다. 과분할은
  상한 재클러스터링과 잔여 프룬으로 복구되지만 **과병합은 복구 수단이 없어** 낮은 쪽으로
  치우친다. 화자 수를 알면 `--speaker-count`가 이 추정을 이긴다.
- **잔여 군집은 화자가 아니다**: 발화 시간 5% 미만 군집의 turn 은 분리기 경계에서 빠진다
  (`stt_diarize.substantial`, `RESIDUAL_SHARE_FLOOR`). 임계값을 낮추면 부스러기 군집이 함께
  생기는데(549.4초 녹음은 1.0 에서 22군집 중 실질 4명), 그것을 화자로 세면 아무도 하지 않은
  말에 화자5..화자22 가 붙는다. 빠진 낱말은 다른 화자로 넘어가지 않고 근거를 잃어 `화자0`
  으로 남는다. 전부 바닥 아래면 전원을 남긴다 — 라벨 0개는 뭉뚱그린 화자보다 나쁘다.
- 파싱된 turn 이 있으면 cap 판정 **전에** stderr 에 항상
  `DIARIZE-CLUSTERS speakers=<N> threshold=<T> turns=<N>` 한 줄을 남긴다. 이 마커는 군집 수,
  실제 임계값, turn 수를 진단하기 위한 기계 판독용 출력이며, 상한 초과여도 남는다.
- `SPEECHTOTEXT_DIARIZE_MAX_SPEAKERS`(기본 `8`): 파싱된 고유 화자 클러스터 수의 상한. 초과하면
  **라벨을 버리지 않고** `--clustering.num-clusters=<상한>`으로 한 번 다시 묶는다(stderr 에
  `DIARIZE-RECLUSTERED speakers=<N> max=<N>`). 그 플래그는 "정확히 k"가 아니라 **"최대 k"**라
  없는 화자를 만들지 않는다 — 133초 2인 샘플에서 k=3·4·6·8 이 모두 화자 2를 냈고, 데이터가
  받쳐주는 15분 회의에서만 k=3·4 가 3·4를 냈다. **다시 묶어도** 상한을 넘길 때만
  `DIARIZE-OVERSEGMENTED`를 남기고 화자 없는 전사로 계속한다. 2인 녹음이 화자 102명으로 나온
  적이 있고(2026-09-05), 그때 라벨을 통째로 버리면 소유자가 받는 것은 화자 없는 문서였다.
- `SPEECHTOTEXT_DIARIZE_MIN_SPEECH`(기본 `0.5`) · `SPEECHTOTEXT_DIARIZE_MIN_SILENCE`(기본 `0.0`):
  sherpa `--min-duration-on/off`. **이름이 닮았을 뿐 하는 일이 다르다** — `on` 은 그 길이 미만
  조각을 버리고, `off` 는 같은 화자의 그 간격 미만을 **재귀적으로 이어 붙인다**.
  `on=0.5` 는 유지한다: sherpa 기본 0.3 은 far-field 에 너무 짧아 불안정한 임베딩이 가짜
  화자를 만든다. `off` 는 `0.8`에서 `0.0`으로 내렸다 — 그 병합이 4.5분 라이프로그를 턴 12개·
  중앙값 16.65초로 만들어 문서에서 발화 구분을 지웠고, 이어 붙인 침묵이 전사 커버리지의
  speech time 까지 부풀렸다(261.8초로 보고, 실제 244.3초). 같은 임계값에서 `off`만 0 으로
  바꾼 2026-09-07 실측은 턴 40→82 · 139→193 이고 **실질 화자 수는 바뀌지 않았다**.
- `SPEECHTOTEXT_DIARIZE_SPEAKERS`(기본 없음): 아는 화자 수를 못박는다. `--speaker-count`와 같은
  자리이고, 선언되면 임계값 추정도 상한 보수도 돌지 않는다. **큰 회의는 과병합될 수 있으므로**(15분 실회의 실측
  1.35=1 · 1.30=2 · 1.10=9) 화자 수를 알면 이쪽이 정답이다.
- `SPEECHTOTEXT_SPEAKER_COUNT_LLM=1`(기본 off): **화자 수 질의 패스**. 1차 분리로 만든 초안을
  모델에게 보여 주고 화자 수만 물어, 답이 지금과 다르면 그 수를 `--clustering.num-clusters` 로
  못박아 **한 번만** 재분리한다. 낱말은 바뀌지 않고 화자 라벨만 바뀐다. 초안은 `unlabelled` 이
  화자 라벨을 걷어낸 상태로 가고 시각은 남는다 — 라벨을 남기면 모델이 그것을 그대로 세어
  돌려준다(실측: 같은 초안에 라벨 있으면 2명, 없으면 3명=기준점). 정수 하나가 아닌 답
  ("3~4"·"세 명"·범위 밖)은 받지 않고, 소유자가 `--speaker-count` 를 선언했으면 묻지 않는다.
  모든 초안을 계정 모델 설정으로 질의한다. stderr에
  `DIARIZE-RECOUNT observed=<N> asked=<N> redo=<N|None>` 한 줄을 남긴다. 질의 실패도 재분리
  실패도 1차 결과를 그대로 쓴다.
- API 백엔드에는 구간 타임스탬프가 없어 화자 분리를 하지 않는다.

### 무음 환각을 줄이는 노브 (기본은 whisper 자신의 값)

짧은 녹음이 `감사합니다` 한 줄로 붕괴하는 것은 whisper 가 무음에서 그럴듯한 말을 만드는
문서화된 현상이다(2026-09-05 실측 2건). 그 완화책을 코드 변경 없이 켤 수 있다 — **아무것도
켜지 않으면 명령줄은 예전과 바이트 동일**하고, 오타·범위 밖 값·없는 VAD 모델 파일은 조용히
무시돼 whisper 자신의 기본값이 선다(설정 실수 하나가 전 녹음을 멈추지 않는다).

| 환경변수 | whisper 플래그 | 쓰임 |
|---|---|---|
| `SPEECHTOTEXT_WHISPER_NO_SPEECH` | `-nth` | 무음 판정을 엄하게 (whisper 기본 `0.60`) |
| `SPEECHTOTEXT_WHISPER_SUPPRESS_NONSPEECH=1` | `-sns` | 비발화 토큰 억제 |
| `SPEECHTOTEXT_WHISPER_BEAM_SIZE` | `-bs` | 빔 폭 (whisper 기본 `5`) |
| `SPEECHTOTEXT_VAD_MODEL` | `--vad -vm` | Silero VAD 전처리 |

VAD 는 조용한 한국어 발화를 잘라낼 수 있어 기본이 아니다 — 환각한 녹음에만 켜고 그 녹음만
다시 돌린다.

## 화자 이름

`화자N` 라벨에 실제 이름을 붙이는 경로는 셋이고, 결과는 헤더의 범례 한 줄로 남는다:

```
- 화자: 화자1=김민수 [자기소개 00:03:12] · 화자2=이영희 [LLM] · 화자3=미상
```

| 출처 | 어떻게 | 표기 |
|---|---|---|
| 자기소개(규칙) | "저는 김민수라고 합니다" 류를 각 화자의 앞 12문장에서만 찾는다. 한 이름을 두 화자가 주장하면 둘 다 버린다 | `자기소개 HH:MM:SS` |
| meeting LLM | `ingest` 가 회의록 CLI 의 마지막 stdout JSON 의 `speakers` 배열을 받아 전사본에 되먹인다(호명·소개처럼 문맥이 필요한 근거는 여기서 나온다) | `LLM` |
| 소유자 | `polish --speakers "화자1=김민수,화자2=이영희"` | `소유자` |

우선순위는 **소유자 > 자기소개 > LLM**. 규칙이 이미 이름을 정한 라벨에 LLM 이 다른 이름을 제안하면
규칙 이름을 유지하고 이견을 범례에 남긴다: `[자기소개 00:03:12 · LLM 제안: 박철수]`. 근거 없는 라벨은
`미상`으로 적는다. 누락과 미상은 다른 사실이다.

**이름은 제안이지 판정이 아니다.** 자기소개 패턴도 LLM 도 틀릴 수 있으므로, 소유자가
`polish --speakers` 로 고치면 그 값이 이후 모든 경로를 이긴다.

이미 있는 전사본은 오디오·모델·비용 없이 다시 다듬을 수 있다(멱등):

```bash
python3 /srv/autophagy-skills/live/speechtotext/scripts/speechtotext_cli.py polish --file <전사본.md>
```

### 화자 등록 (voice catalog) — 등록된 목소리 저장소

`stt_catalog_cli.py` 는 소유자가 지정한 「이 녹음의 화자N = 이름」을 받아 그 화자의 발화
구간을 원음에서 잘라 등록본(16 kHz mono wav, 최대 60초, 블록당 최대 30초)으로 남긴다.
저장은 노드 로컬 `~/.hermes/speechtotext/voice-catalog/`(`SPEECHTOTEXT_VOICE_CATALOG`,
0700/0600, git 체크아웃 안이면 `CATALOG-ROOT-REFUSED`)이고 **음성은 노드 밖으로 나가지
않는다.** 이 단계(②)는 저장소만 만든다 — 등록본을 화자 분리에 결합해 이름을 자동으로
붙이는 것(③)은 아직 없다.

```bash
C=/srv/autophagy-skills/live/speechtotext/scripts/stt_catalog_cli.py
python3 $C propose <전사본.md> [--audio <원음>|--duration-ms N]   # 읽기 전용: 화자N 별 블록·발화·계획
python3 $C enroll --transcript <전사본.md> --speaker 화자2 --name 김민수 [--audio <원음>]
python3 $C list
python3 $C remove --name 김민수                                       # 등록본 파일도 지운다
```

| 규칙 | 내용 |
|---|---|
| **동의 = 소유자의 명시 지시** | `enroll`·`remove` 는 타인의 목소리를 남기는 일이다. 에이전트는 소유자가 **이름을 명시해** 지시했을 때만 돌리고, 스스로 이름을 추측해 등록하지 않는다. `propose` 는 읽기 전용이라 스스로 돌려 제안해도 된다 |
| 원음 출처 | `--audio` 가 없으면 Drive 아카이브 manifest(`~/.hermes/stt-eval/manifest.jsonl`) 에서 전사본 stem 이 같은 행의 `drive_file_id` 를 받아 0700 임시 디렉터리에서 자르고 지운다. 행이 없으면 `CATALOG-AUDIO-MISSING` |
| 구간 | 블록 헤더는 시작 시각만 있으므로 구간 끝 = **다음 블록의 시작**. 마지막 블록은 오디오 길이를 알 때만 쓴다. 1.5초 미만 블록 제외, 긴 블록 우선 |
| 전사본 조건 | **화자가 실제로 갈린 전사본**이어야 한다. ① 이전(옛 임베딩·임계값 1.35)에 만든 라이프로그는 여러 사람이 `화자1` 하나로 뭉쳐 있어(2026-09-17 실측 3건) 거기서 자르면 섞인 목소리가 등록된다 — `plaud_sync_watch.py --reprocess` 로 다시 전사한 뒤 등록한다 |
| 멱등 | 같은 `(recording_id, 화자N)` 재등록은 그 등록본만 교체. 다른 녹음의 같은 사람은 등록본이 늘어난다 |
| 게이트 | `enroll`·`remove` 는 배포 사본에서만 실행(`STALE-SKILL-COPY-BLOCK`, exit 3). 외부효과 승인 게이트 대상은 아니다(로컬 쓰기) |

#### Obsidian 에 적으면 등록된다 (2026-09-17)

CLI 를 부르지 않아도 된다. 라이프로그 노트(vault `000_PARA/Area/Lifelog/`)의 `## 한눈에` 아래에
생성되는 기본 양식의 `미상`을 실제 이름으로 바꾸면 no-agent 워처
`voice_catalog_enroll_watch.py`(10분 틱, `automation/voice_catalog/`)가 RAG 미러에서 그 줄을 읽어
위의 `enroll` 을 돌리고 `owner_notice_channel_id`의 통지 채널에 결과를 올린다
(`#notifications`는 변경 가능한 예시 이름). 기본값 `미상`은 무시하므로 소유자가
이름을 넣기 전에는 등록되지 않는다:

생성 직후:

```
- 화자:: 화자1=미상 · 화자2=미상
```

소유자 교정 뒤:

```
- 화자:: 화자1=홍길동 · 화자2=김민수
```

- 구분자는 `·` `,` `;`, `미상`·`화자0`·빈 이름은 무시, 첫 `- 화자::` 줄만 읽는다. 소유자가 자기
  vault 에 이름을 적은 것이 곧 명령=동의다.
- 같은 이름이 이미 등록됐으면 무동작, 이름을 고치면 재등록(같은 녹음·라벨의 등록본이 교체된다).
  실패(`CATALOG-NO-SEGMENTS`·`TRANSCRIPT-MISSING`·`ENROLL-CLI-MISSING`)는 통지 1건으로 끝나고
  노트 본문이 바뀌기 전엔 다시 시도하지 않는다 — 원장 `<voice-catalog>/obsidian-enroll.json`.
- 미러는 RAG 인제스트가 당겨 오므로 vault 편집이 워처에 보이기까지 그 주기만큼 늦다. 줄을 지워도
  등록은 남는다(되돌리기는 `remove --name`).

### 화자 식별 (voice catalog ③) — 등록된 목소리로 `화자N` 에 이름을 붙인다

등록본이 있으면 전사가 끝난 뒤 **자동으로** 대조한다. 분리기가 만든 `화자N` 군집마다 그 사람이
확실히 말한 블록을 골라 임베딩을 뽑고, 카탈로그의 등록본과 코사인을 재서 판정 세 가지 중
하나를 낸다. 등록본을 녹음 앞에 이어 붙이지 **않는다** — 붙이면 카탈로그가 커질수록 군집
예산(상한 8·임계값)을 먹고 녹음 본체의 군집 경계까지 흔들려 식별하려다 분리를 망친다.

| 판정 | 조건 | 문서에 남는 것 |
|---|---|---|
| **확정** | 점수 ≥ `accept` **그리고** 2위와의 여유 ≥ `margin` — 또는 **차이 판정**: 점수 ≥ `gap_accept`(0.70)이고 같은 녹음의 다른 라벨 최고보다 `gap`(0.10) 이상 높음(라벨 2개 이상일 때만) | 범례 `화자1=김민수 [카탈로그 0.86]` · 블록 헤더에 이름 |
| **제안** | 점수 ≥ `suggest` (확정 아님) | 범례 `화자2=미상 [카탈로그 제안: 이영희 0.72]` — 이름은 붙지 않는다 |
| **미상** | 그 아래 | 아무것도 남지 않는다(`화자3=미상`) |

- **제안을 확정으로 바꾸는 방법은 소유자의 한 줄이다.** 라이프로그 노트에 `- 화자:: 화자2=이영희`
  를 적으면 ②b 워처가 그 녹음에서 등록본을 하나 더 만들고, 다음 녹음부터 점수가 올라간다.
  에이전트는 식별 결과를 그 줄에 **쓰지 않는다** — 쓰면 워처가 자기 제안을 명령으로 읽는다.
- 이름 신뢰 순서는 **소유자 > 카탈로그 > 자기소개 > LLM** 이다. 카탈로그가 자기소개보다 위인
  이유는 라이프로그 전사본 28건에서 자기소개 규칙이 이름을 준 적이 0건이기 때문이다. 어긋난
  자기소개는 지우지 않고 출처에 함께 적는다(`[카탈로그 0.86 · 자기소개 제안: 김민수]`).
- **임계값은 노드 실측값이다**(`docs/qa/VC3`): `accept 0.80` · `suggest 0.65` · `margin 0.05`.
  같은 녹음에서 확실한 양성 0.83, 가장 높은 음성 0.75 였다. 표본이 녹음 1건·등록 1명이므로
  실패 방향을 「확정 대신 제안」으로 잡았고, 등록본이 늘면 같은 표로 다시 잰다. env 로 덮을 수
  있다: `SPEECHTOTEXT_IDENTIFY_ACCEPT`·`_SUGGEST`·`_MARGIN`(제안 문턱이 확정 문턱보다 높으면
  전부 기본값으로 돌아간다).
- **차이 판정**(소유자 결정 2026-10-21): 녹음마다 마이크·방이 달라 소유자 점수가 통째로
  .68-.75 로 내려앉은 녹음이 이어졌다(등록본을 늘려도 +.01-.03). 그런 녹음에서도 다른 라벨은
  .64 를 넘지 않았으므로, `accept` 에 못 미친 제안이라도 점수 ≥ 0.70 이고 같은 녹음의 다른 라벨
  중 최고보다 0.10 이상 높으면 확정한다. 한 라벨 녹음은 비교 상대가 없어 이 길을 타지 않고,
  둘로 갈린 라벨(둘 다 높은 점수)은 차이가 모자라 제안으로 남는다 — 서로 다른 사람일 수 있다.
  env: `SPEECHTOTEXT_IDENTIFY_GAP_ACCEPT`·`_GAP`, 끄기 `SPEECHTOTEXT_IDENTIFY_GAP_RULE=0`.
- **킬스위치** `SPEECHTOTEXT_IDENTIFY=0`. 그 외에도 카탈로그가 비었거나 C API·임베딩 모델이
  없으면 `IDENTIFY-SKIP reason=…` 한 줄을 남기고 전사는 그대로 간다. 식별 실패는 어떤 경우에도
  전사·회의록·라이프로그를 막지 않는다(fail-soft). 진단 표식에는 이름을 싣지 않는다.
- 임베딩은 화자 분리와 **같은 모델**(`SPEECHTOTEXT_DIARIZE_EMBEDDING`)을 stdlib `ctypes` 로 부른다
  (`stt_voiceprint.py` → `<bin>/../lib/libsherpa-onnx-c-api.so`). 새 노드 설정은 없다. 등록본
  임베딩은 `<voice-catalog>/embeddings/<모델 12자>.json`(0600)에 캐시돼 녹음마다 다시 뽑지 않는다.

손으로 물어보려면(읽기 전용, 아무것도 쓰지 않는다):

```bash
python3 $C match <전사본.md> [--audio <원음>]
# 화자1: 김민수 0.86 · 다음 후보 0.31 → 확정
# 화자2: 이영희 0.72 → 제안
```

### 용어집 — 전사 **전에** 주는 힌트 (전사본은 고치지 않는다)

전사본은 증거다. 잘못 들린 낱말도 **들린 그대로** 남기고, 용어 교정은 이 전사본으로 회의록·
라이프로그 노트 같은 **산출 문서를 만들 때** 그 문서에 건다. 절차의 정본은
[용어 교정 규약](../../docs/guide/용어-교정-규약.md) 이다.

왜 여기서 고치지 않는가: 전사본에 새긴 잘못된 교정은 원래 낱말을 지운다. 실제로 `성금=선금`
한 줄이 이 과제의 핵심어 `기성금` 을 `기선금` 으로 깨뜨렸고, 그 표기가 원문에 박혔다면 무엇이
말해졌는지 어디에도 남지 않는다. 문서 단계의 교정은 참고 문서를 고쳐 문서를 다시 만들면
회복된다.

그래서 이 스킬에서 용어집이 하는 일은 하나다 — **인식 조건**. 전사 전에 모델에게
`--prompt 고유명사: 업무, 한전기술` 로 바른 표기를 미리 알려 애초에 맞게 듣게 한다
(`SPEECHTOTEXT_PROMPT` 를 직접 주면 그것이 이긴다).

> **지금은 이 힌트가 인식에 닿지 않는다 (2026-09-22 실측).** 기본 디코딩은 문맥 이월을 끄는 `-mc 0`
> (`SPEECHTOTEXT_WHISPER_CONTEXT` 기본값)인데, whisper.cpp 는 `-mc` 가 0 이면 `--prompt` 도 함께 버린다 — 힌트가 있는
> 후보와 없는 후보의 출력이 바이트 같았다. `--carry-initial-prompt` 로 강제하면 대리 기준 오류율이 오히려 올랐다.
> 이 경로를 걷어 낼지 살릴지는 [후속 과제](../../docs/follow-ups.md#asr-후보-평가-후-남긴-것-2026-09-22)에서 정한다.

참고 문서의 정본은 **Drive 이고, 문서 종류를 따라 중첩**된다. 같은 이름이 겹치면 **깊은 쪽이
이긴다**:

| 어디에 | 무엇에 걸리나 | 세기 |
|---|---|---|
| `autophagy/용어집.csv` | 모든 산출물 | 가장 약함 |
| `autophagy/<문서 종류>/용어집.csv` | 그 종류의 문서(`회의록`·`전사본`·`라이프로그` …) | 중간 |
| `autophagy/<문서 종류>/<과제>/용어집.csv` | 그 과제의 그 문서 | 가장 강함 |

이 스킬이 읽는 종류는 `전사본` 이고, 읽은 이름은 **힌트로만** 쓴다. 같은 회의의 회의록은
`회의록` 층으로, plaud 노트는 `라이프로그` 층으로 **그 문서를 만들 때** 교정된다 — 문서 종류마다
어휘가 다르기 때문이다(회의록은 기관명, 라이프로그는 사람·장소).

층 조회·노드 캐시·Drive 옵트인(`DRIVE_PUBLISH_ENABLED=1`)은 전부 `automation/term_glossary` 가
한다 — 사본을 두면 같은 낱말이 문서마다 달라진다. 조회는 `find_folder_path` 라 **용어집을 찾는
것만으로 폴더가 생기지 않고**, 없는 층은 건너뛴다. Drive 가 답하지 않으면 노드 캐시
(`~/.hermes/term-glossary/transcript.csv`)로 답하며 `GLOSSARY-FETCH-FAIL` 한 줄을 남기고, 답했는데
어느 층에도 없으면 **비어 있는 것이 정답**이라 캐시도 비운다(`GLOSSARY-DRIVE-ABSENT`).

적는 법은 한 줄에 하나이고 **바른 용어 한 칸**이 기본이다 — 틀린 표기는 몰라도 된다:

```
한전기술
영무,업무
```

`#` 로 시작하는 줄은 주석이고 **작성 예시는 [`configs/용어집.example.csv`](configs/용어집.example.csv)
에 각주로 달아 두었다**. `.csv` 인 이유는 Drive 가 표를 Sheets 로 열어 주기 때문이다. 예전
`용어집.txt`(`틀린표기=올바른표기`)도 계속 읽고, 한 폴더에 둘 다 있으면 `.csv` 가 이긴다. 기본은
비어 있다: 추측한 이름을 프로덕션에 적으면 그 오인식을 오히려 굳힌다.

**과제는 파일 이름이 정한다**: `_` 로 나눈 토큰 중 **날짜가 아닌 첫 토큰**. `20260825_해양고신뢰성.m4a`
와 `해양고신뢰성_킥오프.m4a` 는 같은 과제이고, 이름이 날짜뿐이면 과제 없음(예전처럼 연도 폴더에
바로 놓인다). `--project` 로 언제든 덮어쓸 수 있다. 전사본은 `전사본/<과제>/<YYYY>/` 에, 회의록은
meeting 이 같은 과제 이름으로 `회의록/<과제>/<YYYY>/` 에 놓는다.

## Drive 감시 폴더

`SPEECHTOTEXT_DRIVE_FOLDER` 에 지정한 폴더만 본다(운영값 `autophagy/회의녹음` — 산출물과 같은 루트 아래의 유일한 입력 폴더). **미설정이면 아무것도 하지 않는다**
(어떤 폴더인지 추측하지 않는다). 소유자 본인만 접근 가능한 파일만 처리하고(공유된 파일은
`SPEECHTOTEXT-SKIP reason=not-owner-only`), 회의록 생성이 성공한 뒤에만 처리 완료로 기록해
실패한 틱은 다음 틱에 다시 시도한다. 폴링 대상은 Drive이므로 실시간 에이전트의 Discord
메시지와 경쟁하지 않는다.

전사본은 `~/.hermes/speechtotext/transcripts/`(0700)에 정본으로 남고,
`DRIVE_PUBLISH_ENABLED=1` 이면(끄려면 **값 `0`** — CLI 가 `~/.env.secrets` 를 환경에 싣기
때문에 `unset` 으로는 막히지 않는다) 공용 파사드로 `autophagy/전사본/<YYYY>/` 에
best-effort 발행된다(실패해도 로컬 전사본과 회의록은 그대로 진행).

## 사용 중 평가 자료 수집

로컬 전사는 평가용 가설 스냅샷을 함께 남긴다. Drive 워처는 오디오 SHA와 원본 Drive id를
`STT_EVAL_ROOT/manifest.jsonl`에 묶고 스냅샷을 비공개 평가 루트로 옮긴다. 수집 실패는
`STT-EVAL-` 진단으로 드러나며 기존 전사·회의록 성공 판정을 바꾸지 않는다.
소유자가 고친 전사본은 별도 `stt_eval_capture_watch`가 읽어 정답으로 모은다. 원문·정답·가설은
체크아웃 밖에만 저장하며, `automation.stt_eval`의 비교 보고는 모델 자동 교체를 하지 않는다.

## 설정

| 키 | 뜻 | 기본 |
|---|---|---|
| `SPEECHTOTEXT_DRIVE_FOLDER` | 감시할 Drive 폴더 경로(`/` 구분, 운영값 `autophagy/회의녹음`) | **없음 = 무동작** |
| `SPEECHTOTEXT_BACKEND` | `auto` / `local` / `api` | `auto` |
| `SPEECHTOTEXT_WHISPER_BIN` · `SPEECHTOTEXT_WHISPER_MODEL` | whisper.cpp 바이너리·ggml 모델 | PATH 의 `whisper-cli` / 없음 |
| `SPEECHTOTEXT_FFMPEG_BIN` · `SPEECHTOTEXT_FFPROBE_BIN` | 변환·길이 측정 | PATH |
| `SPEECHTOTEXT_LANGUAGE` | 언어 힌트 | `ko` |
| `SPEECHTOTEXT_MODEL` · `SPEECHTOTEXT_BASE_URL` | API 모델·엔드포인트 | `gpt-4o-transcribe` / OpenAI |
| `SPEECHTOTEXT_WHISPER_THREADS` · `SPEECHTOTEXT_LOCAL_TIMEOUT` | 스레드·상한(초) | CPU 수(≤16) / 14400 |
| `SPEECHTOTEXT_ALLOW_INCOMPLETE=1` | 누락·반복 붕괴 의심 전사본도 통과(확인 후에만) | off |
| `SPEECHTOTEXT_MAX_REPEAT` | 반복 붕괴 판정 임계(최다 8어절 점유율) | `0.08` |
| `SPEECHTOTEXT_WHISPER_CONTEXT` | whisper `-mc` 값. `-1`이면 문맥 이월 복원 | `0`(이월 끔) |
| `SPEECHTOTEXT_PROMPT` | 고유명사 힌트(로컬·API 양쪽에 전달). 미설정 시 용어집에서 만든다 | 없음 |
| `SPEECHTOTEXT_GLOSSARY` | 전사 힌트에 쓸 용어집 파일을 **명시**하면 Drive 를 조회하지 않는다(샌드박스·오프라인) | 미설정 = Drive 정본 + 노드 캐시 |
| `SPEECHTOTEXT_TRANSCRIPT_DIR` · `SPEECHTOTEXT_STATE_FILE` | 전사본·처리 상태 | `~/.hermes/speechtotext/` |
| `SPEECHTOTEXT_DIARIZE_BACKEND` · `SPEECHTOTEXT_DIARIZE_MODE` | sherpa / pyannote, pyannote의 regular / exclusive | `sherpa` / `regular` |
| `SPEECHTOTEXT_DIARIZE_BIN` | sherpa 또는 격리 stt-engines 실행 파일. sherpa는 아래 두 모델도 필요 | 없음 = 분리 생략 |
| `SPEECHTOTEXT_ALIGN_BACKEND` · `SPEECHTOTEXT_ALIGN_BIN` | none / whisperx, 격리 정렬 CLI 경로 | `none` / venv 또는 PATH |
| `SPEECHTOTEXT_WHISPER_DTW` | whisper DTW 모델 프리셋 | 없음 = `--dtw` 미사용 |
| `STT_EVAL_ROOT` | 체크아웃 밖 오디오 원장·정답·가설·보고 루트 | `~/.hermes/stt-eval` |
| `SPEECHTOTEXT_DIARIZE_SEGMENTATION` | pyannote segmentation onnx 모델 경로 | 없음 |
| `SPEECHTOTEXT_DIARIZE_EMBEDDING` | 화자 임베딩 onnx 모델 경로 | 없음 |
| `SPEECHTOTEXT_DIARIZE_THRESHOLD` | 군집 임계값(화자 수 미지정일 때만 사용) | `1.0` |
| `SPEECHTOTEXT_DIARIZE_THREADS` | segmentation·embedding 스레드 수 | CPU 수(≤8) |
| `SPEECHTOTEXT_DIARIZE_TIMEOUT` | 화자 분리 상한(초). 넘기면 `DIARIZE-FAIL` 후 계속 | `3600` |
| `SPEECHTOTEXT_DIARIZE_MAX_SPEAKERS` | 군집 수 상한. 넘으면 그 수로 재클러스터링(`DIARIZE-RECLUSTERED`) | `8` |
| `SPEECHTOTEXT_DIARIZE_MIN_SPEECH` · `SPEECHTOTEXT_DIARIZE_MIN_SILENCE` | sherpa `--min-duration-on/off`. `on` 은 짧은 조각을 버리고 `off` 는 같은 화자의 간격을 재귀적으로 이어 붙인다(둘 다 군집 수는 안 바꾼다) | `0.5` / `0.0` |
| `SPEECHTOTEXT_DIARIZE_SPEAKERS` | 아는 화자 수 고정(`--speaker-count` 와 같은 자리) | 없음 = 임계값 추정 |
| `SPEECHTOTEXT_SPEAKER_COUNT_LLM=1` | 전사본 초안에 화자 수를 물어 그 수로 재분리(옵트인) | off = 1차 분리 결과 그대로 |
| `SPEECHTOTEXT_WHISPER_BEAM_SIZE` · `SPEECHTOTEXT_WHISPER_NO_SPEECH` | whisper `-bs` · `-nth` | 없음 = whisper 기본(`5` / `0.60`) |
| `SPEECHTOTEXT_WHISPER_SUPPRESS_NONSPEECH=1` | whisper `-sns`(비발화 토큰 억제) | off |
| `SPEECHTOTEXT_VAD_MODEL` | Silero VAD 모델 경로. 있으면 `--vad -vm` 을 붙인다 | 없음 = VAD 끔 |

## 로컬 전사 도구 설치 (노드 1회)

```bash
git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
cmake -B build && cmake --build build -j --config Release      # build/bin/whisper-cli
sh ./models/download-ggml-model.sh large-v3-turbo-q5_0          # 약 547MiB
```

`~/.env.secrets` 또는 cron 환경에 `SPEECHTOTEXT_WHISPER_BIN`·`SPEECHTOTEXT_WHISPER_MODEL`
을 지정하면 그 다음 틱부터 로컬 경로로 전사한다. `ffmpeg`/`ffprobe` 는 두 경로 모두에서
필요하다(16kHz mono 변환·길이 측정). 품질을 더 원하면 `large-v3-q5_0`(약 1.1GiB), 처리량이
급하면 `medium`. 처리 시간은 CPU 에서 실시간의 1~5배가 걸릴 수 있으므로 cron 틱이 겹치지
않도록 워처가 flock 을 건다.

### GPU 빌드 (NVIDIA GB10 노드, 2026-09-01)

CPU 빌드는 `build/` 에 폴백으로 남기고, `SPEECHTOTEXT_WHISPER_BIN` 은 CUDA 빌드를 가리킨다:

```bash
cmake -B build-cuda -DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=121   # CUDA 13.0
cmake --build build-cuda -j --config Release                        # build-cuda/bin/whisper-cli
```

### 화자 분리 도구 설치 (노드 1회)

sherpa-onnx v1.13.6 CPU 빌드(`linux-aarch64-shared-cpu` 자산)를 agent 계정 홈 `~/sherpa-onnx/` 아래에 둔다:

```
sherpa-onnx/
  bin/    화자 분리 실행 파일 (SPEECHTOTEXT_DIARIZE_BIN)
  lib/    공유 라이브러리 (CLI 가 LD_LIBRARY_PATH 에 자동으로 얹는다)
  models/ sherpa-onnx-pyannote-segmentation-3-0/model.onnx
          3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx
  samples/
```

두 모델 경로를 `SPEECHTOTEXT_DIARIZE_SEGMENTATION`·`SPEECHTOTEXT_DIARIZE_EMBEDDING` 에
적으면 다음 틱부터 화자 블록이 붙는다. 임계값은 임베딩 모델에 붙은 값이라
`SPEECHTOTEXT_DIARIZE_THRESHOLD` 를 함께 적는다 — `eres2net_200k` 는 `0.8`
(2026-09-17 노드 실측, `docs/qa/PLE1/summary.md`; 그 전에는 `eres2net_base` 에 코드
기본값 1.0 이었다). 다른 임베딩으로 바꾸면 같은 표본으로 임계값을 다시 잰다. 화자 분리는 CPU 에서 돌고 전사는 GPU 에서 도므로
둘이 자원을 놓고 다투지 않는다.

## 관련

- 회의록 생성 본체: [`meeting`](../meeting/SKILL.md) — 칸반·통지·Drive 발행 소유
- 발행 규약: [`drive-publish`](../../docs/guide/drive-publish.md)
- 워처 규약: [`watcher-cron-설계규약`](../../docs/guide/watcher-cron-설계규약.md)
- 이번 개선: [전사 정확도 사용 중 개선 루프](../../docs/기능소개/전사-정확도-사용중-개선-루프.md)
- 격리 엔진 설치·토큰·모델 동의 계약: [stt-engines](../../configs/stt-engines/README.md)
- 소개: [`음성-녹취-회의록-자동화`](../../docs/기능소개/음성-녹취-회의록-자동화.md) ·
  [`전사본-화자-구분과-문장-단위-출력`](../../docs/기능소개/전사본-화자-구분과-문장-단위-출력.md)
