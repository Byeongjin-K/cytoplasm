---
name: doctype
description: "문서를 등록·저장·개선하거나 등록된 문서 종류로 초안을 쓰라고 할 때 사용. 문서(.docx/.hwpx/.md/.txt)의 ‘등록/스킬화/저장/개선/문서종류화’ 액션은 주제와 무관하게 항상 doctype이 수행한다. 작성/초안(draft)은 레지스트리에 등록된 이름으로 요청될 때만 doctype 소유이며, 미등록된 제안서 작성은 proposal, 개인 노트 기반 보고서/슬라이드/대본은 report, 발명신고서/선행기술은 patent-prep 스킬로 넘긴다."
version: 1.4.1
author: autophagy-agents
license: MIT
metadata:
  hermes:
    tags: [Document-Type, Private-Store, Versioned-Registry, Codex-Narrative]
prerequisites:
  commands: [python3, hermes]
---

# doctype — 자가개선 서류 종류 라이브러리

변경 명령은 `/srv/autophagy-skills/live/doctype/scripts/`에서만 실행하며, 낡은 사본은 `STALE-SKILL-COPY-BLOCK`으로 거부한다.

새 예시 서류의 제목·구성·항목을 결정적으로 읽고, 작성 목적·톤·논증 방식을 계정 Hermes 설정의 주 모델(폴백 포함, `configs/routing-policy.md`)로
분석해 재사용 가능한 **서류 종류**로 등록한다. 문서의 도메인이 구매/용역이어도 등록·개선·서류 종류화는
이 스킬이 단독 처리한다. `slot-fill`, `narrative`, `hybrid` 모드를 지원한다.
`용역지시서`처럼 필드 중심인 양식은 슬롯을 채우고, `업체추천사유서`처럼 근거가 중요한 양식은 에이전트가
입력 사실과 승인된 사설 예시를 바탕으로 한국어 논증 문단을 직접 작성한다.

## 절대 규칙

1. 예시·승인본·초안의 **본문은 저장·노출 금지**이며 `DOCTYPE_PRIVATE_ROOT`(기본
   `~/.hermes/doctype/private/`, 0700/0600)에만 보관한다. registry의 canonical/overlay에는 본문 없는
   메타데이터와 `private:<opaque>` 참조만 남긴다. `--out`은 repo 내부 경로를 거부한다.
2. 한국어 요지 추출과 서술 초안은 공용 클라이언트(`automation/codex_llm.py`)를 사용한다.
   모델은 계정 `~/.hermes/config.yaml` 이 정한다. 주 모델이
   답하지 못하면 Hermes가 같은 설정의 `fallback_providers`로 넘기며, 그 체인까지 모두 실패하면 `LlmCallError`로 거부한다.
3. `register-from-example`, 같은 이름의 재등록, `refine`은 모두 최대 버전 + 1의 불변 버전을 추가한다.
   승인본은 사설 example으로 누적되어 이후 서술 작성의 few-shot이 된다. 기존 버전은 수정·삭제하지 않는다.
4. `--review`는 문서 본문 없이 파일명·SHA256·type/version만 cha owner에게 보낸다. 제출·메일·외부 수집 기능은 없다.
5. `.docx`/`.hwpx` 예시는 co-deployed procurement reader를 선택적으로 사용한다. placeholder가 있는 private
   template 참조는 `procure_docx.fill`/`procure_hwpx.fill`로 채우며, 이 스킬은 procurement 코드를 수정하지 않는다.

## AGENT WORKFLOW

대화 중 cha가 문서를 첨부하거나 지목하면 gateway가 제공한 내려받은 첨부 경로만 사용한다. 본문을
직접 추출하거나 repo·공개 경로에 복사하지 말고, 아래 CLI만 실행한다. 실행 뒤에는 `show`의 메타데이터로
**name/version/mode/fields만** 요약하고 문서 본문은 절대 채팅에 붙여 넣지 않는다.

### 1. `<종류>로 등록` 또는 `스킬화`

동일 이름이 있는지 확인한다. 있으면 등록 대신 승인 예시를 추가하는 `refine`을 실행한다. 없는 이름이면
`register-from-example`을 실행한다. 사용자가 모드를 명시한 경우에만 마지막 명령에
`--mode slot-fill|narrative|hybrid`를 추가한다.

```bash
CLI="/srv/autophagy-skills/live/doctype/scripts/doctype_cli.py"
if python3 "$CLI" show --name "<사용자가 말한 종류명>" >/dev/null; then
  python3 "$CLI" refine --name "<사용자가 말한 종류명>" --approved "<gateway-첨부파일경로>"
else
  python3 "$CLI" register-from-example --name "<사용자가 말한 종류명>" --example "<gateway-첨부파일경로>"
fi
python3 "$CLI" show --name "<사용자가 말한 종류명>"
```

### 2. `<종류> 개선` 또는 `refine`

개선된 승인 문서를 첨부했을 때만 실행한다. 사용자가 이유를 명시한 경우에만 `--note`를 추가한다.

```bash
python3 "/srv/autophagy-skills/live/doctype/scripts/doctype_cli.py" refine \
  --name "<종류>" --approved "<gateway-첨부파일경로>"
python3 "/srv/autophagy-skills/live/doctype/scripts/doctype_cli.py" show --name "<종류>"
```

### 3. `<종류> 작성` 또는 `초안`

draft 실행 전 `list` 또는 `show --name`으로 해당 이름이 레지스트리에 등록돼 있는지 확인하며, 미등록 이름의 제안서/보고서/발명신고서 요청은 각각 proposal/report/patent-prep 스킬로 넘긴다.
cha가 준 사실을 사설 JSON 파일에만 담고 그 경로를 `--inputs-json`에 준다. `--out`도 사설 경로여야 하며,
repo나 채팅 첨부 경로를 사용하지 않는다.

```bash
python3 "/srv/autophagy-skills/live/doctype/scripts/doctype_cli.py" draft \
  --name "<종류>" --inputs-json "<private-inputs.json>" \
  --out "$HOME/.hermes/doctype/drafts/<파일명>"
python3 "/srv/autophagy-skills/live/doctype/scripts/doctype_cli.py" show --name "<종류>"
```

### 4. 메타데이터 조회

```bash
python3 "/srv/autophagy-skills/live/doctype/scripts/doctype_cli.py" list
python3 "/srv/autophagy-skills/live/doctype/scripts/doctype_cli.py" show --name "<종류>"
```

바이너리 `.hwp`와 잘못된 컨테이너는 표준 `CONVERSION-REQUEST`로 거부한다.
모든 문서는 같은 공용 Hermes 경로와 저장 목적지 규칙을 따른다. 기존 데이터는 그대로.

## Runtime and sandbox

Production goes through the shared client `automation/codex_llm.py`, which runs
`hermes -z … -t todo` (no model, no provider — the account config picks both) with no client-side retry; when the main model cannot
answer, Hermes falls back along the account's `fallback_providers` chain (`configs/routing-policy.md`). Offline tests point `DOCTYPE_HERMES_BIN` at a deterministic local binary (the skill hands it to
the shared client as `AUTOPHAGY_HERMES_BIN`). `scripts/scenario.sh` creates examples, private roots,
stubs, and drafts only under `mktemp`, then removes them.

## 저장 어댑터 운영 설정
Obsidian 저장은 `OBSIDIAN_WRITE_CONFIG`(기본 `~/.hermes/obsidian-write/config.json`)가 가리키는 설정의 전용 `clone_dir`, 읽기 가능한 `ssh_key_path`(쓰기 전용 deploy key), `repo_url`, `branch`를 사용한다. 쓰기 클론은 RAG 읽기 미러와 달라야 하며, key·승인 레코드가 없으면 push 전에 fail-closed한다.

Obsidian 쓰기의 소유자 승인 요청은 cha의 agent-chat 채널 아래 **요청 전용 스레드**에서 열린다 — 스레드 이름은 `옵시디언 · <pending-id>`로 **레코드 id 하나뿐**이며, 노트 제목·경로·본문은 이름에 들어가지 않는다(내용은 스레드 안 승인 본문에만 있다). 대기 레코드는 그 스레드를 `approval_thread_id`로 기록한다.

## Obsidian 노트 저장 요청 (경로·본문을 받은 저장)

"Obsidian에 이 노트를 만들어/저장해 줘"처럼 **경로와 본문이 주어진** 저장 요청(대리 요청 포함)은 아래 명령 하나로만 승인을 받는다. 초안을 손으로 만들거나 소유자에게 **채팅 메시지에 ✅를 눌러 달라고 요구하지 않는다** — 채팅 메시지의 ✅는 아무것도 실행하지 않는다. 승인은 이 명령이 올리는 표준 승인 카드의 ✅ 하나뿐이고, ✅ 뒤 워처(`obsidian-note-watch`, 2분)가 정확히 그 경로·본문을 쓰고 push 한 뒤 원격에서 다시 읽어 해시를 확인하고, 영수증을 같은 스레드에 남긴다.

1. **링크 먼저**: 본문을 쓰기 전에 recall(검색 인덱스)로 관련 노트를 찾고, `[[ ]]` 링크는 **실제로 볼트에 있는 노트 제목**으로만 단다. 볼트에 없는 개념은 링크하지 않고 평문으로 쓴다.
2. 본문을 파일로 저장한 뒤(예: `~/.hermes/tmp/note.md`) 릴리스 런타임에서 실행한다:
   ```bash
   cd /srv/autophagy-agent-current && python3 -m automation.obsidian_write request \
     --relpath '<볼트 전체 경로, 예: 000_PARA/Resource/012_X/2026-10-07_제목.md>' \
     --body-file ~/.hermes/tmp/note.md \
     --origin-channel-id <지시 메시지 채널 id> --origin-message-id <지시 메시지 id>
   ```
   `--relpath` 는 소유자가 준 경로를 **줄이지 않고 그대로** 넘긴다(`000_PARA/`·`001_KIMM_PARA/` 로 시작해야 한다). 본문 첫 줄이 `# 제목` 이면 그것이 노트 제목이 되고 본문에서 빠진다(아니면 `--title`, 그것도 없으면 파일 이름).
3. 출력의 `APPROVAL-THREAD … url=<링크>` 를 답장에 그대로 싣고, 전체 경로를 줄이지 않고 적는다. `LINK-UNRESOLVED [[x]]` 가 있으면 링크를 고치거나 평문으로 바꿔 **같은 경로로 다시 요청**한다(같은 경로의 새 요청은 소유자가 아직 누르지 않은 이전 카드를 대체한다). `RELATED [[x]]` 는 넣을 만한 관련 노트 후보다.
4. 결과 확인은 `python3 -m automation.obsidian_write status` — `status=written` 과 `remote=`·`sha256=` 이 영수증이다. `NOTE-REQUEST-REFUSED reason=owner-decided` 는 이전 카드가 이미 결정됐다는 뜻이다(워처가 처리한다).

Drive 저장은 공용 `automation.drive_outputs` 파사드를 통해 `autophagy/문서/<YYYY>/<YYYY-MM-DD>_<원제목>.<확장자>`에 이름+부모 기준 upsert한다. 날짜는 최초 저장일로 고정되므로 같은 문서를 다시 저장해도 사본이 아니라 기존 파일이 갱신된다. owner-only permission과 재다운로드 SHA-256를 모두 확인해야 성공이다. 규약 정본: `docs/guide/drive-publish.md`.

## 문서 저장 목적지 라우팅

저장 목적지는 **산문이 아니라 코드가 결정한다** — `doctype_routing.classify_save_request()`가 판정하고 `doctype_cli`가 mutation 직전에 강제한다(`cmd_register`/`cmd_draft`/`cmd_refine`). 아래는 그 가드의 판정 순서이며, 기존의 "Obsidian과 Drive에 항상 동시 저장" 규칙은 폐기됐다.

1. 사용자가 Obsidian·Google Drive·로컬·둘 다 등 저장 목적지를 명시하면 그 지시를 최우선으로 따른다.
2. 사용자가 **"개인노트 저장"**, 개인 노트 저장 또는 Obsidian 저장을 요청하면 적절한 PARA 폴더에 canonical Markdown으로 저장한다. Drive 동시 저장을 별도로 요청하지 않았다면 Drive에는 업로드하지 않는다.
3. 보고서·공고문·안내문·계획서 등 파일 산출물을 생성·저장하라고 하면서 목적지를 말하지 않으면 cha 본인 Google Drive를 기본 목적지로 사용한다. 공개·링크 공유 권한 없이 비공개로 업로드하고, 재다운로드 SHA-256과 owner-only permissions를 확인한다.
4. 단순 대화 답변·아이디어 작성처럼 파일 저장 의도가 없는 요청은 외부 저장으로 확대 해석하지 않는다.
5. 같은 이름의 파일은 중복 생성하지 않고 결정적 경로에서 갱신한다. 한쪽만 성공하면 완료로 보고하지 않고 부분 실패를 명시한다.

판정 불가 시 동작: **모호하면 저장하지 않고 되묻는다** — `SAVE-CLARIFY reason=... candidates=...`를 출력하고 **exit 5**로 종료하며 외부 저장 부작용은 0건이다. 선택된 목적지는 `doctype_save.save_artifact()`가 실행한다. Obsidian 목적지는 RAG 미러가 아닌 **분리된 쓰기 클론**에서 소유자 승인 게이트를 거쳐 commit·push하고 원격 read-back 해시로 검증한다(SI-5 개정). Drive 목적지는 private upsert 후 owner-only permission과 재다운로드 SHA-256를 검증한다. 어느 한쪽이라도 실패하면 `DOCTYPE-REFUSED`로 실패하며 성공을 보고하지 않는다.

사용 안내: [docs/기능소개/저장-목적지-라우팅.md](../../docs/기능소개/저장-목적지-라우팅.md)
