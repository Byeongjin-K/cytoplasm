# 문서 글자 읽기(OCR) 연결 — 릴리스 스위치로 켜기

## 무엇을

공용 본문 추출기 `automation/document_text.py` 가 글자 층이 없는 PDF(스캔본), 구형 `.hwp`, 사진(`.png`·`.jpg`)을 만나면, 노드에 설정된 외부 글자 읽기 명령에 넘겨 표까지 살린 markdown 본문을 받는다. 설정이 없으면 지금과 똑같이 사유만 돌려준다.

## 왜

메일 첨부나 참고자료에는 스캔본 PDF와 구형 한글 파일이 많다. 지금은 "텍스트 레이어가 없습니다", "hwpx 나 pdf 로 저장해 주세요"로 끝나서 요약·검색·회의록 근거에서 빠졌다. 글자 읽기 모델은 GPU가 필요하고 노드마다 있는 것이 아니므로, 코드에 모델을 넣지 않고 명령 하나로 바깥에 맡긴다. 명령이 없는 노드는 동작이 바뀌지 않는다.

## 켜는 법

노드에서는 릴리스 스위치로 켠다(형 Q335 2026-10-21 "머지하고 다음 릴리스 때 켜기"). 릴리스 호스트에 `~/.hermes/release-flags/document-ocr` 를 정확히 `on` 으로 두면 다음 릴리스 카드에 "메일 첨부·스캔 PDF·구형 HWP·사진 글자 읽기(OCR) 켜기" 한 줄이 실리고, 형이 그 카드에 ✅ 하면 배포기가 agent 계정에 표식 `~/.hermes/document-ocr.mode`(`on`)를 놓는다. 표식이 정확히 `on` 이면 추출기는 `automation/document_ocr.py relay` 를 부른다 — 파일을 그 계정 `~/outbox/ocr/` 에 두면 운영자 계정의 글자 읽기 중계기가 GPU 큐의 격리 상자(네트워크·홈 없음)에서 읽고 `~/inbox/ocr/` 로 답을 돌려준다. 게이트·방화벽·sudoers 는 바뀌지 않는다. 파일을 지우고 다음 릴리스를 내면 꺼진다.

다른 설치나 시험에서는 런타임 환경에 `DOCUMENT_OCR_CMD` 를 둔다(표식보다 먼저 읽힌다). 값은 `<명령> [인자...]` 이고, 추출기는 끝에 파일 경로를 붙여 부른다. 명령은 markdown 을 stdout 에 내고 exit 0 으로 끝나야 한다. 오래 걸리는 큐 명령이면 `DOCUMENT_OCR_TIMEOUT_SECONDS`(기본 1800초)를 조정한다. 값을 지우면 꺼진다.

## 사용 시나리오

**happy path — 스캔본 참고자료**

```
DOCUMENT_OCR_CMD=<글자 읽기 명령>
참고자료 폴더의 스캔본.pdf → pdftotext 결과가 비어 있음
→ <글자 읽기 명령> 스캔본.pdf → 표가 든 markdown
→ Extracted(status="ok"), 회의록·제안서 인제스트가 그 본문을 근거로 씀
```

**happy path — 메일 첨부(유돌)**

```
형: "어제 공고 메일 첨부 hwp 뭐라고 써 있어?"
→ 유돌: mail-list 로 uid 확인 → mail-attachment-text <uid> <첨부 이름> + 형 글 좌표
→ 노드: 형 본인 24시간 안 글인지 확인 → 형 메일함에 저장된 그 첨부만 추출기에 넣음
→ ATTACHMENT-TEXT status=ok … 한 줄 + 앞부분, 전체 본문은 job-file 로
→ 유돌이 요약해 답함
```

글자 읽기가 필요한 첨부는 한 쪽에 1~2분 걸리므로 바로 끝나지 않으면 `JOB-RUNNING id=…` 가 오고 유돌은 `job <id>` 로 다시 본다.

글자 층이 있는 PDF는 글자 읽기를 부르지 않는다(지금처럼 `pdftotext` 만). 참고자료 조회의 내려받기 전 판정(`reference_rank.refusal`)도 켜져 있을 때만 `.hwp`·사진을 받아들인다.

**거부 경로**

- 설정 없음: 구형 `.hwp` 는 "구형 hwp 는 hwpx 나 pdf 로 저장해 주세요", 스캔본은 "텍스트 레이어가 없습니다(스캔본이거나 빈 자료)" — 지금과 같다.
- 명령이 실패하거나 빈 결과: "글자 읽기(OCR)로도 본문을 얻지 못했습니다". 예외를 던지지 않으므로 회의록 작성 등은 계속된다. 중계기가 30분 가까이 답하지 않으면 클라이언트가 요청을 거둬들이고 같은 사유로 끝난다.
- 메일 첨부 읽기: 형 글 좌표가 형 본인 글이 아니거나 24시간이 지났으면, 형 메일함에 없는 첨부면 거절된다. 꺼진 노드에서 스캔본·구형 hwp 첨부는 `status=refused` 와 위 사유를 돌려준다.

## 관련

- 코드: `automation/document_text.py`(`ocr_command`·`readable_suffixes`·`_from_ocr`), `automation/document_ocr.py`(릴리스 스위치·표식·중계 클라이언트), `automation/document_ocr/deploy.sh`, `automation/reference_rank.py`(`refusal`), `automation/yoodori_gate/yoodori_read_gate.py`(`mail-attachment-text` 잡)·`yoodori_request_runtime.py`(`attachment_text`)
- 시험: `tests/unit/test_document_text.py`, `tests/unit/test_reference_rank.py`, `tests/unit/test_document_ocr_release.py`, `tests/unit/test_document_ocr_relay.py`
- 승인·게이트: 켜기는 형 ✅ 를 받는 릴리스 카드 한 줄(같은 HEAD 승인 표면 `document-ocr:on`)이다. 첨부 읽기는 형 계정 안의 읽기라 카드가 없고, 형 본인 글에서 시작한 요청만 받는다. 중계기는 처리 뒤 운영자 계정 쪽 입력·결과·임시 파일을 지우고 기록에는 이름·바이트·sha256 만 남긴다.
