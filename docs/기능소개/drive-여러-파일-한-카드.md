# Drive 저장 — 여러 파일을 카드 하나·승인 한 번으로

## 무엇을

`drive-save` 가 `--file` 을 여러 번 받는다. 파일마다 카드가 뜨던 것을 카드 하나에 파일 목록(이름·크기·sha256 앞부분)으로 모으고, 형은 ✅ 를 한 번만 누른다. 저장할 곳은 표준 트리(`--kind`·`--project`) 또는 형 Drive 의 아무 폴더 id(`--folder`)다. 유돌 게이트는 `mail-attachment <uid> --all` 로 메일 첨부를 한꺼번에 staging 에 복사한다.

## 왜

이슈 #241: 메일 첨부 여러 개를 Drive 에 올리려면 파일마다 승인 카드가 떠서 하나하나 눌러야 했다. 형 결정(2026-11-13): Q43 "짱돌이 새 기능으로 만들어", Q548 "아무 폴더 id나 허용", Q549 "나머지는 올리고 파일별 결과를 남겨 실패한 것만 다시 시도".

## 사용 시나리오

**메일 첨부 셋을 한 번에.** 형: "이 메일 첨부 다 드라이브 받은 첨부 폴더에 저장해 줘."

1. 유돌: `mail-attachment <uid> --all`(끝에 형 글 좌표) → 노드가 저장된 첨부를 모두 staging 에 복사하고 파일마다 `STAGED …` 한 줄. 노드에 없는 첨부는 `NOT-STAGED …` 로 알리고 나머지는 계속한다(최대 20개).
2. 유돌: `drive-save --file mail-<uid>-1.pdf --file mail-<uid>-2.hwp --file mail-<uid>-3.xlsx --summary '<설명>' --folder <폴더 id>`(끝에 형 글 좌표). 받은 첨부는 원래 이름으로 저장된다.
3. 카드(판본 `effect-request-v2`): `파일 3개(합계 …) — 이 목록 전체가 승인 해시에 묶인다` 아래 파일별 한 줄, `저장 폴더: <이름> (경로 …, id …)`, 비공개 안내. 형 ✅ 한 번.
4. 워처가 파일마다 비공개로 올리고 다시 내려받아 sha256 을 확인한 뒤, 같은 스레드에 파일별 ✅ 와 링크를 남긴다.

노드에 「Drive 저장 자동 쓰기」 스위치가 켜져 있으면 2단계가 카드 없이 바로 저장된다(`save-now`, 파일마다 `OWNER-COMMAND-WRITTEN` 한 줄).

**일부만 실패하면.** 셋 중 하나가 Drive 오류로 실패해도 나머지는 올라간다. 파일별 결과(성공·실패·사유)가 요청 기록에 남고, 다음 워처 틱(2분)이 같은 승인으로 실패한 파일만 다시 올린다(최대 5번). 끝내 실패하면 스레드에 `❌ 일부만 저장됨 … 3개 중 2개 저장` 과 파일별 사유가 남는다. 카드 없는 저장은 그 자리에서 실패한 파일만 세 번까지 다시 시도하고, 하나라도 남으면 `DRIVE-SAVE-FAILED <이름> <사유>` 와 함께 종료 코드 1.

**거절되는 경우.**
- 목록이 바뀌면(파일 추가·내용 변경·폴더 변경) 해시가 달라 새 카드가 필요하다. 승인 뒤 동결 사본이 바뀐 파일은 그 파일만 저장하지 않는다.
- 다른 사람과 공유된 폴더, 폴더가 아닌 id, 휴지통의 폴더는 요청 때와 저장 직전 둘 다 거절한다. 공유는 메일 `compose --drive-share` 로만 한다.
- 파일 21개 이상, 카드가 1900자를 넘는 목록(폴더째·많은 파일은 [폴더 트리 한 카드](drive-폴더-트리-한-카드.md)), 같은 Drive 이름이 둘인 목록, `--folder` 와 `--project` 를 함께 쓴 요청은 아무것도 저장하지 않고 이유를 낸다(나눠서 요청).
- 할 일 근거(`--origin-todo`)로 저장할 때는 모든 파일이 그 할 일 근거 메일의 첨부여야 한다.

## 관련

- 코드: `automation/effect_request/`(cli·render·execute·watch·gate), `automation/drive_outputs.py`(`folder_target`·`save_to_folder`), `automation/drive_client.py`(`folder_info`), `automation/yoodori_gate/yoodori_read_gate.py`(`drive-save` 반복 인자·`--kind`·`--folder`, `stage_all_attachments`)
- 승인: 기존 `external-effect` 카드(💾)와 워처 `effect-request-watch` 그대로, 새 카드 종류·워처 없음. 한 줄 파일 저장(폴더 없음)은 예전 v1 카드·해시 그대로.
- 시험: `tests/unit/test_effect_request_batch.py`, `tests/unit/test_yoodori_staging.py`, `tests/unit/test_yoodori_mail_attachment.py`, `tests/unit/test_yoodori_gate_todo_origin.py`
- 이전 문서: [모든 요청에 승인 경로](모든-요청-승인-경로.md), [유돌 형 명령 통로](유돌-형-명령-통로.md)
