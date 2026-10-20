# 문서 글자 읽기(OCR) 연결 — 기본 꺼짐

## 무엇을

공용 본문 추출기 `automation/document_text.py` 가 글자 층이 없는 PDF(스캔본), 구형 `.hwp`, 사진(`.png`·`.jpg`)을 만나면, 노드에 설정된 외부 글자 읽기 명령에 넘겨 표까지 살린 markdown 본문을 받는다. 설정이 없으면 지금과 똑같이 사유만 돌려준다.

## 왜

메일 첨부나 참고자료에는 스캔본 PDF와 구형 한글 파일이 많다. 지금은 "텍스트 레이어가 없습니다", "hwpx 나 pdf 로 저장해 주세요"로 끝나서 요약·검색·회의록 근거에서 빠졌다. 글자 읽기 모델은 GPU가 필요하고 노드마다 있는 것이 아니므로, 코드에 모델을 넣지 않고 명령 하나로 바깥에 맡긴다. 명령이 없는 노드는 동작이 바뀌지 않는다.

## 켜는 법

노드 런타임 환경에 `DOCUMENT_OCR_CMD` 를 둔다. 값은 `<명령> [인자...]` 이고, 추출기는 끝에 파일 경로를 붙여 부른다. 명령은 markdown 을 stdout 에 내고 exit 0 으로 끝나야 한다. 오래 걸리는 큐 명령이면 `DOCUMENT_OCR_TIMEOUT_SECONDS`(기본 1800초)를 조정한다. 값을 지우면 꺼진다.

## 사용 시나리오

**happy path — 스캔본 참고자료**

```
DOCUMENT_OCR_CMD=<글자 읽기 명령>
참고자료 폴더의 스캔본.pdf → pdftotext 결과가 비어 있음
→ <글자 읽기 명령> 스캔본.pdf → 표가 든 markdown
→ Extracted(status="ok"), 회의록·제안서 인제스트가 그 본문을 근거로 씀
```

글자 층이 있는 PDF는 글자 읽기를 부르지 않는다(지금처럼 `pdftotext` 만). 참고자료 조회의 내려받기 전 판정(`reference_rank.refusal`)도 켜져 있을 때만 `.hwp`·사진을 받아들인다.

**거부 경로**

- 설정 없음: 구형 `.hwp` 는 "구형 hwp 는 hwpx 나 pdf 로 저장해 주세요", 스캔본은 "텍스트 레이어가 없습니다(스캔본이거나 빈 자료)" — 지금과 같다.
- 명령이 실패하거나 빈 결과: "글자 읽기(OCR)로도 본문을 얻지 못했습니다". 예외를 던지지 않으므로 회의록 작성 등은 계속된다.

## 관련

- 코드: `automation/document_text.py`(`ocr_command`·`readable_suffixes`·`_from_ocr`), `automation/reference_rank.py`(`refusal`)
- 시험: `tests/unit/test_document_text.py`, `tests/unit/test_reference_rank.py`
- 승인·게이트: 없음. 명령은 노드 안에서 파일을 읽어 글을 돌려줄 뿐 바깥으로 쓰지 않는다. 명령이 문서를 다른 기계로 보낸다면 그 명령 쪽에서 처리 뒤 지우기를 보장해야 한다.
- 메일 첨부는 아직 이 추출기를 부르지 않는다(후속 과제).
