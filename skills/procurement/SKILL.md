---
name: procurement
description: "구매·용역 요청 문서를 등록된 양식으로 채워 달라고 할 때 사용. 이미 등록된 구매/용역 템플릿에 품목·금액·업체를 채워 초안 파일을 생성하고 cha에게만 검토 요청을 보낸다. 새 문서의 등록·스킬화·문서 종류 저장·개선은 doctype 소유이며, 이 스킬은 새로 받은 문서를 템플릿으로 저장하지 않는다."
version: 1.2.4
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Procurement, Documents, GWS, Autophagy]
prerequisites:
  commands: [python3]
---

# 구매/용역 서류 초안 (procurement)

변경 명령은 /srv/autophagy-skills/live/procurement/scripts/에서만 실행하며, 낡은 사본은 STALE-SKILL-COPY-BLOCK으로 거부한다.

이미 등록된 cha의 기관 양식에 값을 채워 **초안 파일**을 만들고 검토를 요청한다.

## 라우팅 경계 (필수)

이 스킬은 **이미 등록된 구매/용역 양식에 품목/금액/업체를 채워 초안을 생성**할 때만 사용한다.
사용자가 문서를 `등록/스킬화/문서 종류로 저장/개선`하려 하면 이 스킬이 아니라 **doctype** 스킬을
사용하라. 새로 받은 문서를 템플릿으로 무단 저장하지 말 것.

## 절대 규칙 (안전)

1. **제출은 항상 인간**: 이 스킬은 어떤 문서도 외부(기관 시스템/메일/타인)로
   제출·발송하지 않는다. 유일한 발신은 cha 본인에게 보내는 검토 요청 DM이다.
2. **바이너리 .hwp로는 생성 시도 금지**: preflight가 `CONVERSION-REQUEST`(exit 3)를
   내면 그 안내문을 그대로 cha에게 전달하고 **생성을 시도하지 않는다**
   (한글에서 HWPX로 변환한 사본을 요청). 지원 포맷: docx/xlsx/hwpx.
3. **필수 항목 누락 시 생성 거부**: `GENERATION-REFUSED`(exit 5)가 나열한 누락
   항목을 cha에게 질문해 채운 뒤에만 다시 시도한다. 값을 추측하지 마라.
4. **금액/업체명은 공개 채널·repo에 게시 금지**: 수집한 값과 생성 파일은 cha DM과
   agent 홈(`~/.hermes/procurement/`) 밖으로 내보내지 않는다.

## CLI (`PY=python3`)

### 1) 등록된 양식 확인

새 양식을 등록하지 않는다. 이미 등록된 이름과 필드만 조회하고, 해당 양식의 값 채우기 요청만 처리한다.

```bash
$PY /srv/autophagy-skills/live/procurement/scripts/procure_cli.py templates-list
$PY /srv/autophagy-skills/live/procurement/scripts/procure_cli.py templates-show --name po_form
```

Registry는 `~/.hermes/procurement/templates/<name>/`에 private template과 분석 맵
(`form_map.json` 또는 `placeholders.json`), `meta.json`을 0700/0600 권한으로 보관한다.

### HWPX/DOCX 기계식 채움 보장

- **HWPX**: 등록 시 vendored MIT `hwpx-generator` form mapper가 빈 표 셀/`<hp:t/>`를
  주소화한다. adapter가 왼쪽·같은 셀 앞 문단·위 라벨 순서로 `label_association`,
  `slot_type=empty_input`, `zone=detail`을 정한다. 이후 저장 `form_map.json`은
  honeypot `slot_filler`가 순수하게 소비하므로 LLM/네트워크 없이 재현 가능하다.
- **DOCX**: stdlib ZIP/XML로 `word/document.xml`을 unpack하고, 동일 formatting의 인접
  `<w:r>`을 합쳐 `{{필드}}`를 바꾼 뒤 rezip/parse 검증한다. Word가 token을 여러 run으로
  나누어도 동작하며 proprietary docx skill 스크립트는 포함하지 않는다.
- **XLSX**: 기존 `openpyxl` 셀 치환 경로를 유지한다.

### 2) 등록된 양식의 필드 수집

`templates-show --name <등록된-이름>`의 fields 순서대로 cha에게 물어보고, 받은 값을
`fields-json`으로 건넨다. 금액 등 형식이 맞지 않으면 사유를 전달하고 다시 묻는다. 새로 받은
문서를 대상으로 `collect-start`나 `register`를 실행하지 않는다. `일자`는 미지정 시 오늘(KST)이다.

### 3) 저장 양식 재사용 초안 생성 + 자동 검증

```bash
$PY …/procure_cli.py generate --template-name po_form \
  --fields-json '{"품목":"…","금액":"1,234원","업체":"…"}' \
  --out ~/.hermes/procurement/drafts/<이름>.hwpx
```

성공 시 `GENERATED` + `VERIFIED parser=…`가 나온다. 포맷별 read-back으로 필수 필드를
재확인한다. `GENERATION-REFUSED`(exit 5)면 누락 목록을 cha에게 안내한다.

### 4) 검토 요청 — 유일한 발신

```bash
set -a; . ~/.env.secrets; set +a
$PY …/procure_cli.py review --file <생성파일> --note "<한 줄 요약>"
```

25MiB 이하는 파일 첨부, 초과는 공용 파사드 `automation.drive_outputs`로 `autophagy/구매/<YYYY>/<YYYY-MM-DD>_<파일명>`에 업로드한 뒤 링크를 DM한다(`DRIVE_PUBLISH_ENABLED=1`일 때만; 날짜는 최초 발행일 고정이라 재발행해도 사본이 늘지 않는다). 상세: `docs/guide/drive-publish.md`.
DM 말미의 "제출은 cha가 직접" 문구를 지우지 마라.

## Sandbox scenario

`scripts/scenario.sh`: 합성 템플릿(docx/xlsx/hwpx/.hwp)을 즉석 생성해 preflight,
누락 거부, .hwp 0-생성, HWPX와 run-split DOCX의 **register→store→reuse→fill→검증**,
XLSX 경로, 검토 DM 크기 분기(스텁)를 검증하고 `SCENARIO-PASS`를 출력한다.
네트워크 호출·실시크릿 없음.
