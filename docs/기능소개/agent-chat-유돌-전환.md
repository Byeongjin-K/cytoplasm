# agent-chat 대화를 유돌에게 전환

## 무엇을·왜

P3 전환은 **기본 꺼짐**이다. 켜면 agent 계정의 Hermes 게이트웨이가 `#agent-chat`에서 답하지 않고
준비된 유돌(YooDori)이 대화를 맡는다. 도리를 설치하지 않은 환경의 응답을 끊지 않으면서
두 에이전트가 같은 요청에 답하는 일을 막는다.
Hermes 최상위 `discord.ignored_channels`가 `free_response_channels`·허용 목록보다 우선한다.
승인 카드·리액션 워처·메일 등 기존 실행 경로를 옮기는 변경은 아니다.

켜짐 여부는 릴리스 호스트의 `~/.hermes/release-flags/dori-agent-chat` 파일 하나로 정한다.
내용은 대화를 받을 도리 봇의 숫자 ID뿐이어야 한다(공백·줄바꿈도 넣지 않는다).
파일이 없으면 기존 메모리 프로필을 적용하고 이 채널의 무시만 제거한다.
잘못된 내용이나 판독 실패는 꺼짐으로 넘어가지 않고 거부한다.

채널 ID는 노드 계정의 `~/.hermes/interop/config.json`의 `agent_chat_channel_id`에서만 읽는다.
노드는 릴리스 플래그를 읽지 않고 호스트가 전달한 `--dori-agent-chat on|off`를 받는다.
agent의 `hermes-agent-profile-v1`이 메모리 상한·자동 회상과 이 선택을 함께 적용한다.
백업 한 번 → 임시 파일 → 원자 교체 → 두 설정 재조회 순서이며,
peer는 기존 `hermes-memory-profile-with-recall-v1`을 유지한다.
`activation=gateway`가 릴리스의 agent+peer 동시 재시동을 요구한다.

## 사용 시나리오

- **기본:** 전환 파일 없이 릴리스하면 기존 메모리 설정만 맞춘다. 채널이 free 목록에 있으면
  doctor는 “도리 전환 꺼짐 — agent-chat 채널은 @ 없이도 답한다”를 PASS로 표시한다.
- **켜기:** 유돌 수신을 준비한 뒤 릴리스 호스트에 위 파일을 만들고 숫자 봇 ID만 저장한다.
  표준 릴리스 카드에 다음 줄이 **한 번** 표시된 것을 보고 승인한다.

  > 도리 전환 켬: 노드 agent 게이트웨이가 #agent-chat 무시(유돌이 받음)

  `dori-agent-chat:<봇 ID>`와 그 지문이 승인 해시에 묶인다. 배포기가 agent 설정을 맞추고
  두 게이트웨이를 재시동한다. 이후 `#agent-chat`에 말하면 Hermes agent는 답하지 않는다.
  doctor는 “agent-chat 채널은 유돌이 맡는다(이 게이트웨이는 무시)”를 PASS로 표시한다.
  free 목록에 같은 채널이 남아 있어도 ignored가 우선하며, 시작 전 자동 등록기는 무시 목록을 지우지 않는다.
- **승인 누락:** 전환 파일은 있지만 배포 HEAD의 승인 카드가 같은 봇 ID를 포함하지 않거나
  소유자 ✅를 확인하지 못하면 `DORI-SWITCH-UNAUTHORIZED`로 배포를 막고 노드 설정은 보존한다.
- **설정 거부:** 켜짐에서 채널 ID가 없으면 종료 코드 3, 쓰기·백업 없음이다.
  꺼짐·`--show`에서는 interop 설정이나 채널 ID가 없어도 오류가 아니며 `agent_chat_ignored=no`다.
  중복 키·인라인 Discord 매핑·별칭·해석할 수 없는 목록도 고치지 않는다.
  숫자 ID의 쉼표 문자열·한 줄 목록·블록 목록은 지원한다. `--check`는 불일치 1,
  재조회 불일치는 5다. 기존 메모리 프로필의 외부 제공자·상한 낮추기 거부(7)도 유지한다.
- **읽기 점검:** agent 계정의 릴리스 런타임에서
  `python3 -m automation.memory_provider.agent_config --show`로 두 설정을 함께 본다.
  `--check`는 `--dori-agent-chat on|off`를 명시해야 한다.
  `agent_chat_ignored=yes`는 저장된 ID가 무시 목록에 있다는 뜻이며 ID 자체는 출력하지 않는다.

## 유돌의 승인 카드 요청

유돌은 SSH 강제명령으로 기존 읽기(`calendar`, `mail-list`, `mail-get`, `recall`, `status`),
Gmail 읽기(`gmail-list`, `gmail-get`)와
아래 **승인 요청 6종**, 노드 비공개 폴더로의 `stage` 파일 전달만 할 수 있다.
실행·승인·발송·워처 호출 권한은 없다.
기존 카드의 소유자 ✅ 뒤에만 노드의 기존 워처가 실행한다. 릴리스 카드에는 전환 표면이 있을 때만
`유돌 게이트: 카드 올리기 명령 6개 추가(실행 권한 없음)`이 전환 켬 줄 다음에 붙는다.
그 다음 줄에는 `유돌 게이트: Gmail 읽기 2개 추가`도 표시된다.
개수는 렌더러의 고정 상수이며 테스트가 게이트 명령 목록 길이와의 일치를 강제한다.

아래 표에서 `S`는 `/srv/autophagy-skills/live`, `P`는 `/usr/bin/python3`다.
모든 요청에 `--origin-channel-id <숫자> --origin-message-id <숫자>`가 필수이며 그대로
기존 생산자에 전달된다. 일정의 두 번째 호출은 첫 초안에 저장된 좌표를 사용한다.

| SSH 명령 | 고정 생산자 argv (`P` 뒤) | 허용 입력 |
|---|---|---|
| `mail-compose` | `S/mail/scripts/triage_cli.py compose` | 필수 `--to --subject --body`, 선택 `--cc --bcc --in-reply-to` |
| `calendar-request` | `S/calendar/scripts/calendar_cli.py draft-create`, 성공 시 같은 CLI `post-confirm --draft <반환 id>` | 필수 `--text`, 선택 `--summary` |
| `todo-request` | `S/todo/scripts/todo_cli.py request` | 필수 `--title`, 선택 `--notes --due`; tasklist는 기본 `@default` |
| `drive-save` | `-m automation.effect_request request drive-save` | 필수 `--file --summary`, 선택 `--title --project`; kind는 기본 `doctype` |
| `command-request` | `-m automation.effect_request request command … -- <argv>` | 필수 `--summary --target --argv-b64`; cwd·timeout은 기존 CLI 기본값 |
| `obsidian-request` | `-m automation.obsidian_write request` | 필수 `--relpath`, `--body-file` 또는 `--body-b64` 중 하나, 선택 `--title` |

### PC에서 파일 전달: `stage <name>`

제한 SSH 키에는 scp/sftp가 없으므로 파일은 **같은 SSH 명령의 stdin**으로 전달한다.

```bash
ssh <노드-별칭> 'stage report.pdf' < ./report.pdf
```

게이트는 원시 바이트를 agent 홈 `.local/share/yoodori-staging/report.pdf`에 저장하고
`STAGED report.pdf sha256=<64자리 hex> bytes=<바이트 수>`를 출력한다.
이름은 `[A-Za-z0-9._-]{1,120}`이고 선두 점·슬래시는 금지한다. 파일은 최대 64 MiB다.
임시 파일(0600)에 받아 해시·크기를 계산하고 끝까지 받은 뒤 이름을 원자 교체한다.
초과·읽기 실패는 `YOODORI-GATE-REFUSED`이며 부분 파일을 지우고 기존 완성 파일은 보존한다.
폴더는 0700이고 이 단계는 노드 비공개 저장일 뿐, Drive·Obsidian에 쓰거나 카드를 올리지 않는다.

전달 후 `drive-save --file <노드-agent-home>/.local/share/yoodori-staging/report.pdf …`로
기존 승인 카드를 요청한다. 형의 카드 ✅ 이후에만 Drive에 저장한다.
노트 본문은 별도 업로드 없이 `obsidian-request --relpath … --body-b64 <UTF-8-Base64> …`로
줄 수도 있다. 런타임이 검증한 본문을 같은 비공개 폴더의 해시 기반 `.md`로 저장한 뒤
기존 CLI의 `--body-file`로 넘기며, 본문 두 입력을 함께 주거나 둘 다 생략하면 거부한다.
원 지시 좌표와 본문 8,000자 상한은 그대로다.

Gmail 읽기는 원 지시 좌표 없이 사용한다.

| SSH 명령 | 고정 gws argv | 검증·출력 |
|---|---|---|
| `gmail-list <n> [query words...]` | `gws gmail users messages list --params '{"userId":"me","maxResults":n,"q":"query"}'`, 각 id에 `messages get --params '{"userId":"me","id":"…","format":"metadata"}'` | n=1~50, query≤200자(선택, 없으면 q 생략). `{status:"ok",mails:[{id,from,subject,date,snippet}]}` |
| `gmail-get <id> [--body]` | `gws gmail users messages get --params '{"userId":"me","id":"…","format":"metadata 또는 full"}'` | id는 소문자 16진수 8~32자. `{status:"ok",mail:{id,from,subject,date,snippet,body?}}` |

“Gmail에서 안 읽은 메일 5개 보여 줘” → `gmail-list 5 is:unread` →
목록을 읽고 각 메시지의 메타데이터만 조회해 한 줄 JSON으로 돌려준다.
`gmail-get <id> --body`만 full 형식을 요청해 MIME의 인라인 plain 본문을 우선 읽고
없으면 HTML을 텍스트로 바꾼다. 첨부파일 조회·다운로드는 하지 않는다.
잘못된 id·쿼리·범위는 `YOODORI-GATE-REFUSED`이고, gws 실패·깨진 응답은
`{"status":"error","error":"gmail-read-failed"}` 및 종료 코드 3이다. gws 오류 출력이나 토큰은 돌려주지 않는다.
gws가 JSON 앞에 `Using keyring backend: keyring`을 출력해도 첫 `{`부터 JSON을 읽는다.
stderr는 섞지 않으며 그 안내나 자격증명 오류 출력은 응답으로 보내지 않는다.
`send drafts modify labels trash delete batchModify insert import watch settings`는 모두 게이트에서 거부한다.

- **메일 회신 예:** “이 메일에 이렇게 답장해 줘” → 유돌이 원문 UID를 `--in-reply-to`로
  넘긴 `mail-compose` 요청 → 원문 인용을 포함한 기존 메일 승인 카드 → 형 ✅ → 기존 메일 워처 발송.
  일반 `draft --instruction`에는 원문 좌표 인자가 없으므로 허용하지 않는다.
- **요청자 표시:** 생산자에 requester/source 인자는 없다. 카드 발신자는 기존 **노드 봇**이며
  `YooDori`라는 새 요청자 이름이 카드 본문에 추가되지는 않는다. 대신 형의 원 지시 메시지 좌표를
  항상 바인딩한다. 그 메시지가 agent-chat에 있으면 그 메시지에 승인 스레드가 매달린다.
  다른 채널의 지시는 기존 공용 승인 표면 규칙을 따르며, 좌표의 작성자 신원을 이 게이트가 검증하지는 않는다.
  유돌은 CLI의 `APPROVAL-THREAD … url=` 링크를 답장에 그대로 인용한다.
- **위키:** `wiki_cli draft`는 초안만 만들고 카드 게시 CLI가 없으며 원 지시 좌표도 받지 않는다.
  임의 `python -c`를 열지 않기 위해 이번 게이트에는 넣지 않는다. 기존 Hermes 위키 경로는 그대로다.

### 입력·거부 경계

각 옵션은 한 번만, `--옵션 값` 쌍으로 준다. 알 수 없는 옵션·추가 위치 인자는 거부한다.
제목·요약·대상·과제·경로는 240자, 일정 원문은 1,000자, 본문·메모는 8,000자 상한이다.
빈 값·제어문자는 거부하며 본문에만 탭·줄바꿈을 허용한다. ID는 양수 숫자 1~20자리,
이메일은 엄격한 단일 주소(표시 이름·쉼표 목록 없음), `--due`는 시간대 있는 ISO 날짜·시각이다.
메일 CC/BCC도 각 한 주소만 받는다.

SSH 원문에는 기존 셸 금지문자(`; & | 백틱 $ < > 역슬래시`)와 제어문자를 계속 거부한다.
본문에 이런 글자나 줄바꿈이 필요하면 UTF-8 본문을 **표준 Base64**로 만들어
`--body-b64`로 보낸다. `--notes-b64`, `--text-b64`, `--subject-b64` 등 텍스트 옵션도 같다.
게이트는 디코딩한 값을 **하나의 argv 값**으로 전달하며 셸에 넘기지 않는다.
원문 전체는 요청 48,000자, 기존 읽기는 그대로 600자 상한이다.

파일은 노드 agent 홈의 `.local/share/yoodori-staging/` 아래에 **이미 있는**
64 MiB 이하 일반 파일만 허용한다. `..`, 디렉터리, 밖으로 향하는 심링크, 없는 파일,
메일 첨부·본문 파일·공유 등 허용 목록 밖 파일 옵션은 거부한다.
배포기가 이 비공개 staging 디렉터리(0700)를 준비하고, `stage`가 그 안에만 파일을 받는다.
Obsidian의 목적지는 `000_PARA/` 또는 `001_KIMM_PARA/` 아래 안전한 `.md` 상대 경로다.

`--argv-b64`는 JSON 문자열 배열을 Base64로 인코딩한다(1~32개).
첫 항목은 `/usr/bin/<소문자 명령명>`이며 각 값은 비어 있지 않은 8,000자 이하 문자열이다.
이 배열은 **실행하는 대신 카드에 동결**한다. 셸 문자·제어문자·실행 계열 토큰을 거부하며
`approve execute send apply watch resolve confirm --yes mailon deploy sudo sh bash dash zsh
python python3 perl ruby node bun env`는 명령·경로 토큰에서도 허용하지 않는다.
그 뒤에도 공용 `effect_request.policy`가 소유자 전용 작업·전용 스킬 효과를 거부한다.
예를 들어 `mailon send`, `calendar confirm`, `todo create`, 직접 watcher 경로는 모두
`YOODORI-GATE-REFUSED`로 끝나며 자식을 실행하지 않는다.

게이트는 SSH 환경을 자식에게 넘기지 않는다. 읽기 환경은 기존 그대로이고,
카드 요청만 노드 `.env.secrets`의 봇 토큰·소유자 ID와 고정 릴리스 import 경로를 사용한다.
로그는 명령 이름·rc·초만 기록한다. 카드 게시 실패나 일정 초안 id 판독 실패는
실행으로 우회하지 않고 실패로 남긴다.

### 선언 배포

`automation/yoodori_gate/deploy-manifest.txt`:

```text
agent|automation/yoodori_gate/yoodori_read_gate.py|.local/libexec/yoodori-read-gate|v2:file
agent|automation/yoodori_gate/gmail_read.py|.local/libexec/gmail_read.py|v2:file
agent|automation/yoodori_gate/yoodori_request_runtime.py|.local/libexec/yoodori_request_runtime.py|v2:file
```

릴리스의 전량 수렴이 provenance 검사 뒤 `push_file`의 해시 재조회로 설치하고 0755를 확인한다.
v2 파일 선언은 홈 기준 상대 경로를 지원하므로 기존 `HOME_DEPLOYED_PATTERN`을 넓히거나
legacy 중앙 표를 변경할 필요가 없다. `authorized_keys`·접속 PC·키·sudo 권한은 변경하지 않는다.
릴리스 렌더러는 게이트를 import하지 않는다. 카운트 상수의 일치만 테스트에서 대조하므로
`deploy-skill.sh`의 `GATE_HELPERS`나 `interop_runtime`에 게이트 파일을 더하지 않는다.

## 되돌리기

릴리스 호스트의 `~/.hermes/release-flags/dori-agent-chat`를 **삭제하고 재배포**한다.
호스트는 `off`를 전달하며 기존 합성 선언 그대로 해당 채널만 무시 목록에서 뺀다.
다른 무시 채널·메모리 설정을 보존하고 agent+peer를 함께 재시동한다.
코드·선언 변경이나 노드의 수동 `--remove`는 필요 없다.

## 관련

- 선언·배포: `automation/memory_curator/{deploy-manifest.txt,deploy.sh}`, 공용 `automation/deploy_config.sh`
- 합성: `automation/memory_provider/agent_config.py`, 목록 편집: `automation/hermes_agent_chat_ignore.py`
- 점검: `automation/doctor/capabilities.py`, [연결·승인 점검](../guide/연결-승인-점검.md)
- 회귀: `tests/unit/test_hermes_agent_chat_ignore.py`, `test_hermes_agent_profile.py`, `test_doctor.py`
- 승인: 기존 릴리스 승인으로만 배포하며 별도 카드·워처는 추가하지 않는다.
