# drive-publish — Drive 산출물 발행 규약

스킬이 만든 **최종 산출물**은 소유자(cha) 본인 Google Drive의 폴더 **하나**에 모인다.
이 문서가 그 규약의 단독 정본이다 — 폴더 구조·이름·단일 사본·발행 경로·환경변수·
마이그레이션 절차를 여기서만 정의하고, 다른 문서(AGENTS.md·SKILL.md)는 요약과 링크만 둔다.

구현은 `automation/drive_taxonomy.py`(이름·배치 순수 로직)와
`automation/drive_outputs.py`(발행 파사드)가 나눠 갖는다. Drive 호출은 기존 `gws` CLI로만
하며 Python SDK·서비스계정·새 외부 의존은 없다.

> git 추적 문서(.omo/plans·notepads, docs/)를 Drive로 미러링하던 drive-archive(E11)는
> 2026-07-31 폐기됐다 — 대상이 전부 GitHub에 있어 미러가 중복이었다. 부활시키지 않는다.

## 단일 루트와 경로

```
<DRIVE_OUTPUTS_ROOT="autophagy">/<카테고리 폴더>/[<과제>/]<YYYY>/<날짜프리픽스 파일 | 번들 폴더>/<파일>
        depth 1                 depth 2      depth 3    depth 3|4          depth 4|5
```

루트를 depth 1로 세고 **depth 5가 상한**이다. 번들 안의 파일이 그 상한에 걸리며, 그보다 얕은
배치는 허용된다(예: 예산 시트는 `<root>/예산/<YYYY>/<시트>` = depth 4 — 과제×년도 시트가
년도 폴더에 직접 놓인다). 상한을 넘기는 요청은 `TaxonomyError`로 거부된다. 모든 이름은
NFC로 정규화한다.

## 입력 폴더 하나

이 루트에는 산출물만 있는 것이 아니다. **`<root>/회의녹음`** 은 소유자가 음성 녹취를 **놓는**
입력 폴더이고, `speechtotext` 워처가 그것만 본다(`SPEECHTOTEXT_DRIVE_FOLDER`, 미설정=무동작).
카테고리 레지스트리의 대상이 아니며 발행 파사드는 이 폴더를 만들지도 쓰지도 않는다 — 여기 적는
이유는 하나다. 같은 루트 아래 있으니 규약이 그 존재를 알아야 한다.

## 과제(프로젝트) 폴더 — 선택적 한 단

카테고리와 연도 사이에 **과제 이름 한 단**을 넣을 수 있다:

```
<root>/전사본/해양고신뢰성/2026/2026-08-26_20260825_해양고신뢰성.md
<root>/전사본/해양고신뢰성/용어집.csv
<root>/회의록/해양고신뢰성/2026/2026-08-26_회의록-….md
```

**과제는 레지스트리 항목이 아니라 호출 인자다** — 연도와 같은 성격이다. 레지스트리가 소유하는
것은 카테고리 **폴더 이름**뿐이고, 과제는 그 안에서 자료를 묶는 축이다. `publish(...)` 와
`folder_parts(...)` 에 `project=` 를 주지 않으면 경로는 **예전과 정확히 같다** — 그래서 과제를
쓰지 않는 스킬(주간동향·제안서·예산·구매·문서·특허)은 이 변경에 영향받지 않는다.

과제를 쓰면 파일이 **정확히 depth 5**에 놓인다. 따라서 **과제 + 번들 조합은 depth 6이 되어
`TaxonomyError` 로 거부된다** — 지금 과제를 쓰는 두 카테고리(전사본·회의록)는 산출물이 하나뿐이라
해당이 없고, 그 조합이 필요해지면 상한을 먼저 논의해야 한다는 뜻이다(조용히 늘리지 않는다).

같은 과제 안에서 같은 제목을 다시 발행하면 날짜 프리픽스는 **그 과제 폴더 안에서** 찾아 재사용한다
(sticky). 과제 밖을 뒤지지 않으므로 다른 과제의 동명 산출물과 섞이지 않는다.

`용어집.csv`(예전 이름 `용어집.txt` 도 계속 읽는다)는 과제 폴더 바로 아래(depth 4)에 놓이는 **입력**이다 — 발행 파사드는 그것을 만들지도
쓰지도 않고, `speechtotext` 가 읽기만 한다. 회의록 양식 파일(이름에 양식·서식·템플릿·template 이
들어가는 `.md`/`.markdown`/`.txt`)도 같은 자리에 놓이는 소유자 입력이다.

## 날짜 없는 상태 파일

날짜 프리픽스도 연도 폴더도 갖지 않는 파일이 두 종류 더 있다. 산출물이 아니라 스킬이 **읽고 다시
쓰는 상태**이며, 위치는 다음과 같이 고정이다.

| 파일 | 경로 | depth | 소유 |
|---|---|---|---|
| `action-items.csv` | `<root>/회의록/<과제>/action-items.csv` | 4 | meeting — 그 과제의 미결·완료 action item 전체 |
| `project-codes.csv` | `<root>/회의록/project-codes.csv` | 3 | meeting — 과제 → 관리번호 영문 4자 코드 배정 레지스트리 |

두 파일 다 **누적**이다. 완료 행은 지우지 않고, 한 번 배정된 코드는 회수하지 않는다. 코드가
전 과제에 걸쳐 유일해야 관리번호를 나중에 한 통으로 모을 수 있기 때문에, 레지스트리는 과제
폴더가 아니라 카테고리 폴더에 산다.

진입점은 `automation.drive_outputs` 의 두 함수다.

```python
publish_state_file(parts, name, local, client=None) -> str   # upsert + owner-only + sha256 재검증
fetch_state_file(parts, name, dest, client=None) -> bool     # 없으면 False, 예외 아님
```

`parts` 는 `drive_taxonomy.category_parts(kind)`(루트/카테고리) 또는 `project_parts(kind, project)`
(루트/카테고리/과제)로 만든다 — 폴더 이름을 하드코딩하지 않는다. 두 함수는 날짜 프리픽스를 붙이지
않고 sticky 날짜도 찾지 않으며, 이름 그대로 upsert 한다(단일 사본 규칙은 동일). 상태 파일 읽기·쓰기
역시 파사드 밖에서 `gws drive` 를 직접 부르는 것은 금지다.

## 카테고리 레지스트리

`automation/drive_taxonomy.CATEGORIES`가 유일한 출처다. 호출부는 폴더명을 하드코딩하지 않고
kind만 넘긴다. 새 카테고리는 레지스트리에 한 줄 추가로 끝난다.

| kind | 폴더 | periodicity | gate_only | always_bundle |
|---|---|---|---|---|
| `report` | 주간동향 | weekly | - | ✔ |
| `proposal` | 제안서 | oneshot | - | - |
| `budget` | 예산 | monthly | - | - |
| `meeting` | 회의록 | oneshot | - | - |
| `transcript` | 전사본 | oneshot | - | - |
| `audio` | 녹음원본 | oneshot | - | - |
| `procurement` | 구매 | oneshot | - | - |
| `doctype` | 문서 | oneshot | - | - |
| `patent` | 특허 | oneshot | ✔ | - |
| `share` | 공유 | oneshot | - | - |

**스킬이 소유한 카테고리(`skill_owned`)는 손으로 발행할 수 없다.** `meeting`·`transcript` 는
그 스킬의 파이프라인이 산출물에 부수효과를 붙이는 자리다 — 회의록은 action-item 원장과 관리번호를,
전사본은 용어집 교정을 거기서 얻는다. 파이프라인을 건너뛴 문서는 폴더에서 똑같아 보이면서 정확히
그것만 빠져 있다. 그래서 `drive_publish_cli` 가 `--kind meeting|transcript` 를 Drive 호출 전에
`DRIVE-PUBLISH-REFUSED`(exit 2)로 거부하고 올바른 명령을 안내한다. **파사드에는 이 가드가 없다** —
스킬 자신이 파사드를 거쳐 발행하기 때문이다. 레지스트리가 그 사실의 단일 출처다.

**`share`(공유)는 owner-only 가 아닌 유일한 카테고리다(2026-10-14).** `publish` 가 아니라
`drive_outputs.share` 가 올리며, 발행과 달리 소유자가 메일 승인 카드에서 확인한 사람에게만
`type=user, role=reader` 권한을 더한다. 재다운로드 대신 Drive `sha256Checksum`·크기로 바이트를
확인하고(수백 MB 파일을 다시 받지 않기 위해서다), 권한은 부여 전·후에 "소유자 + 지정한 사람(reader)"
외의 것이 하나라도 있으면 `ShareRefused` 로 멈춘다 — 링크 공개(anyone)·도메인·편집 권한을
만드는 경로는 없다. 폴더·파일명은 `share_target` 이 요청 시점에 정해 승인에 묶고, 폴더는
`autophagy/공유/` 밖을 가리킬 수 없다. 스킬 소유 카테고리라 손 발행은 거부되며 호출자는
mail `compose --drive-share` 뿐이다([소개](../기능소개/메일-Drive-공유-링크.md)).

`gate_only` 카테고리(patent)는 일반 발행 경로에서 `TaxonomyError`로 거부된다. 특허 산출물은
전용 반출 게이트(`patent_export`)만 만질 수 있고, 이 규약은 그 폴더의 **위치**만 정한다.

`budget` 카테고리(폴더 `예산`)는 분류 어휘로 남아 있다. 이 폴더에 시트를 옮기던 budget 스킬은
2026-10-22 형의 요청으로 제거됐고, 마이그레이션 CLI도 더는 예산 시트를 옮기지 않는다.

## 기간 키

날짜는 언제나 이름 **앞**에 붙는다. 폴더를 월별로 파지 않는다.

| periodicity | 키 | 예 |
|---|---|---|
| weekly | ISO 주 `YYYY-Www` | `2026-W34_주간보고` |
| monthly | `YYYY-MM` | `2026-08_예산요약` |
| oneshot | 최초 생성일 `YYYY-MM-DD` (고정) | `2026-08-23_회의록-킥오프.md` |

oneshot의 날짜는 **고정(sticky)** 이다. 같은 제목을 다시 발행하면 파사드가 카테고리 폴더의
연도들을 최신순으로 훑어 기존 파일의 날짜 프리픽스를 찾아 재사용한다 — 재생성해도 이름이
바뀌지 않으므로 사본이 늘지 않는다. ISO 주 키는 연말·연초에 입력 날짜의 달력 연도와 다를 수
있고, 그럴 때 연도 폴더는 **주 키의 연도**를 따른다.

## 단일 사본 upsert

(이름, 부모) 조합이 파일의 정체성이다. 같은 조합이 이미 있으면 그 `fileId`를 **그대로 두고
내용만 갱신**한다(update-in-place). 링크와 리비전 히스토리가 보존된다.

**delete + recreate 금지.** 새 파일을 만들고 옛 것을 지우면 공유 링크가 죽고 리비전이 끊긴다.

업로드마다 owner-only 권한 검증과 재다운로드 SHA-256 대조를 통과해야 성공으로 친다. 하나라도
어긋나면 실패다(fail-closed).

## 번들과 companion

- 산출물이 **2개 이상**이거나 카테고리가 `always_bundle`이면(report) 번들 폴더를 만든다:
  폴더는 depth 4(`<period>_<제목>`), 그 안의 파일은 depth 5.
- 산출물이 하나뿐이면 번들 없이 파일이 곧 depth 4의 리프다.
- **companion**은 최종 산출물에 딸린 원본 자료(예: 제안서 이미지 프롬프트 원본)다. 소유자가
  `--companion`으로 **명시 지정한 것만** 올라가며, 자동 발견·일괄 업로드는 금지다. companion은
  날짜 프리픽스를 붙이지 않고 **원본 파일명을 그대로** 유지한다. companion이 하나라도 있으면
  산출물이 하나여도 번들이 만들어진다.

## 올리지 않는 것

- 위키 본문 — 소유자 확인 게이트가 있는 별도 저장소다.
- prompt 스킬의 버전 관리 자산 일괄 미러링.
- 에이전트 작업용 코드와 계획 문서(`.omo/`, `docs/`).
- 특허 산출물 — `patent`는 gate_only, 전용 반출 게이트 전용이다.

유일한 예외는 위의 명시적 `--companion` 지정이다.

## 신규 스킬 준수 의무

**Drive 산출물 발행은 `automation.drive_outputs` 파사드만 쓴다.** 스킬 안에서 `gws drive`를
직접 부르거나 헬퍼를 vendoring해 폴더를 만들고 파일을 올리는 것은 금지다 — 사본이 생기는
순간 규약이 갈라지고 중복 파일이 다시 쌓인다.

강제 수단은 `tests/unit/test_drive_outputs_conformance.py`다. 파사드 밖의 Drive 업로드·폴더
생성이 발견되면 테스트가 실패한다. 새 스킬은 다음만 하면 된다:

```python
from automation.drive_outputs import publish_best_effort

publish_best_effort("procurement", 제목, [(파일경로, 산출물제목)])
```

`publish_best_effort`는 `DRIVE_PUBLISH_ENABLED=1`이 아니면 조용히 `None`을 돌려주고 아무것도
호출하지 않는다. 실패는 `DRIVE-PUBLISH-FAIL kind=... reason=...` 한 줄로 축약되어 로컬 산출물
경로를 절대 흔들지 않는다. 실패를 그대로 터뜨려야 하는 경로는 `publish`를 직접 쓴다.

## 세션에서 손으로 발행하기

스킬이 아니라 **세션이 만든 산출물**(요청받아 그 자리에서 만든 문서·표)도 같은 트리에 들어간다.
파사드를 감싼 CLI 하나가 그 경로다.

```bash
python3 -m automation.drive_publish_cli \
  --kind doctype --title "용역공정표-템플릿" --project 해양고신뢰성 \
  --on 2026-08-26 ./용역공정표-템플릿.xlsx
```

- 업로드 로직을 갖지 않는다. 인자를 파싱해 `publish(...)` 를 부를 뿐이라 두 번째 발행 경로가 생기지 않는다.
- `publish_best_effort` 가 아니라 `publish` 를 부른다. 손으로 돌리는 사람에게 침묵은 성공으로 읽힌다.
  `DRIVE_PUBLISH_ENABLED` 가 1 이 아니면 `DRIVE-PUBLISH-DISABLED` 로 **실패**한다(exit 3).
- 모르는 kind, gate-only kind(`patent`), 없는 파일은 Drive 를 건드리기 전에 거부한다(exit 2).
- **워크스테이션에는 `gws` 가 없다.** 파일을 노드로 옮긴 뒤 `agent` 로, `/srv/autophagy-agent-current`
  를 `PYTHONPATH` 에 두고 실행한다. 임시 사본은 발행 직후 지운다.
- 발행 뒤 대상 폴더를 **다시 조회해 파일이 1개인지 확인한다**. 파사드의 sha256 재검증은 내용을
  보증하지만 사본 수는 보증하지 않는다.

## 환경변수

| 변수 | 기본 | 용도 |
|---|---|---|
| `DRIVE_PUBLISH_ENABLED` | (미설정) | `1`일 때만 발행. 옵트인 — 미설정 환경과 테스트에서는 Drive 호출 0 |
| `DRIVE_OUTPUTS_ROOT` | `autophagy` | 단일 루트 폴더명 |
| `DRIVE_GWS_BIN` → `DRIVE_PUBLISH_GWS_BIN` | `gws` | gws 바이너리(앞의 것이 우선) |
| `DRIVE_PUBLISH_CACHE` | `~/.hermes/drive-publish/folders.json` | 폴더 id 조회 캐시 |

## 마이그레이션 (1회성, 소유자 실행)

레거시 `<type>/<YYYY-MM>/` 배치와 흩어진 예산 시트·특허 폴더를 새 트리로 옮긴다. **dry-run이
기본**이고 모든 mutation은 `--apply`에만 있다. 이동은 ID를 보존하는 move이며, 중복 패자와 빈
레거시 폴더는 **휴지통으로만** 간다(영구 삭제 없음).

```bash
python3 -m automation.drive_migrate_outputs            # 계획만 출력 (MIGRATE-PLAN ...)
python3 -m automation.drive_migrate_outputs --apply    # 계획 실행 (MIGRATE-VERIFY ...)
```

읽는 법:

- `MIGRATE-PLAN move|rename|trash|skip …` — dry-run에서 검토할 계획. `skip`은 이유가 붙는다
  (`invalid-date`, `unknown-legacy-folder`, `already-migrated`, `missing-env:…` 등).
- `MIGRATE-VERIFY id=… owner-only=ok` — apply가 이동 직후 권한을 재확인한 증적.
- `MIGRATE-GUIDANCE delete-stale-cache path=…` — 옛 폴더 캐시 파일을 지우라는 안내.
  지우지 않으면 캐시가 옛 폴더 id를 가리킨다.
- `MIGRATE-ERROR …` — 실패. rc 1로 멈추며, 남은 항목은 손대지 않는다.

예산 시트는 옮기지 않는다(budget 스킬 제거, 2026-10-22). 특허 보관 폴더는 `PATENT_ARCHIVE_FOLDER_ID`로 찾는다. 모두 ID 참조라 이동
후에도 코드·게이트는 그대로 동작하며, 재실행 시 이미 년도 폴더 안에 있는 시트는
`already-migrated`로 건너뛴다(멱등).

## 관련

- 이름·배치 규칙: `automation/drive_taxonomy.py`
- 발행 파사드: `automation/drive_outputs.py`
- 공유 Drive 클라이언트: `automation/drive_client.py`
- 마이그레이션 CLI: `automation/drive_migrate_outputs.py`
- 저장 목적지 라우팅(개인노트 vs Drive): `skills/doctype/scripts/doctype_routing.py`

## 롤아웃 체크리스트

이 체크리스트는 이 계획의 **작업 종결(배포까지)** 인수인계다. 워크트리에서는 실배포가 불가하므로
실행 주체는 PR 머지 후 소유자 또는 후속 세션이며, 이 문서 안에서 실제 배포·마이그레이션을 실행하지 않는다.

① **PR 머지 및 릴리스 태그:** cha가 PR을 머지한 후 `automation/release-tag.sh`를 실행한다. 릴리스 태그 규칙상 태그가 없으면 프로덕션은 전진하지 않는다.

② **노드 코드 반영:** 노드 ops의 ff-pull은 2분 리컨실러가 자동으로 수행한다.

③ **스킬 재배포:** `automation/deploy-skill.sh <skill> --request-only`를 {report, proposal, doctype, procurement, meeting} 각각에 요청한다. 소유자 ✅ 후 자동 마운트되며, `readlink /srv/autophagy-skills/live/<skill>`로 판정한다.

④ **노드 환경변수 정리:** 노드 `~/.env.secrets`에서 `DRIVE_PUBLISH_PERIOD`, `DRIVE_DOCTYPE_ROOT`, `DRIVE_DOCTYPE_CACHE`, `PROCURE_DRIVE_ROOT`, `PROCURE_DRIVE_PERIOD`를 제거한다. `DRIVE_PUBLISH_ENABLED=1`, `DRIVE_OUTPUTS_ROOT`, `DRIVE_GWS_BIN`는 유지한다.

⑤ **소유자 마이그레이션:** `python3 -m automation.drive_migrate_outputs`를 실행해 dry-run을 검토한 뒤 `python3 -m automation.drive_migrate_outputs --apply`를 실행한다. 출력의 verify 라인을 확인하고 `~/.hermes/doctype/drive-folders.json`, `~/.hermes/drive-publish/folders.json` 캐시 파일을 삭제한다.

⑥ **사후 확인:** 스킬을 1회 실행해 새 경로로 업로드되는지 확인한다.
