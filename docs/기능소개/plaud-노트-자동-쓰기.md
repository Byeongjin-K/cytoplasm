# Plaud 노트 자동 쓰기

소유자 결정 Q231(2026-10-18 17:39 KST):
> 카드 없이 바로 옵시디언에 쓰기 (형 정책대로, 쓴 건 하루 요약에 남음)

Plaud가 전사·요약을 끝내 동결한 노트를 별도 승인 카드 없이 기존 Obsidian 쓰기 경로로
저장한다. 녹음마다 카드를 누르는 부담을 없애되, 본문 SHA 확인·공유 클론 잠금·원격 내용
재조회 검증·실패한 틱 재시도는 유지한다. 기본은 꺼짐이며 기존 카드는 그대로 처리한다.

## 켜기와 되돌리기

릴리스 호스트의 `~/.hermes/release-flags/auto-write-plaud`에 줄바꿈 없이 정확히 `on`을 둔다.
릴리스 카드는 `auto-write:plaud` 표면을 승인 해시에 묶고 다음 줄을 표시한다.

`Plaud 노트 자동 쓰기: 녹음 노트를 카드 없이 옵시디언에 씀(쓴 건 매일 21:30 요약)`

그 릴리스의 소유자 승인 뒤 배포기가 agent의 `~/.hermes/auto-write/plaud.mode`를
0600으로 원자 배치하고 다시 읽어 확인한다. 노드 수작업은 없다. 호스트 파일을 삭제하고
다음 릴리스를 배포하면 노드 설정이 없어져 카드 흐름으로 돌아온다.
값이 틀리거나 해당 HEAD의 승인이 없으면 켜지지 않는다.

## 사용 시나리오

- 켜진 상태에서 새 녹음의 전사·추출이 완료된다 → 동결 본문 SHA를 확인한다 →
  기존 `obsidian_write.write_note`로 저장하고 원격 내용을 검증한다 → `written`이 된다.
  요청 스레드나 승인 카드는 만들지 않는다.
- 동결 뒤 본문이 바뀌면 저장하지 않는다. 쓰기·검증·원장 기록 실패 시 완료로 표시하지 않고
  기존 틱에서 재시도한다. 꺼진 상태에서는 예전처럼 건별 승인 카드를 올린다.
- 매일 21:30 KST에 `## 📝 오늘 자동으로 쓴 것` 아래 `### Plaud 노트`와
  녹음 시각·노트 경로를 한 줄씩 보낸다. 본문은 보내지 않는다.
  빈 날은 침묵하고, 발송 실패는 워터마크를 옮기지 않아 다음 틱에 재시도한다.
  같은 날짜의 성공한 요약은 다시 보내지 않는다. 21:30 이후 저장한 노트와 실패한 요약의
  미보고 노트는 다음 성공 요약에 이월해 빠뜨리지 않는다.
  목록이 Discord 한 메시지 한도를 넘으면 같은 제목의 메시지 하나에 전체 목록 파일을 붙인다.

요약은 기존 `owner_notice.notify_owner`를 쓰므로 채널 합치기·notice-bot 전환이 적용되면
유돌 채널에 유돌 봇으로 간다. 별도 토큰·채널 상수는 없다.
타이머 이름은 `auto-write-daily-summary`, 선언 주기는 `30 21 * * *`, `runner=systemd`다.
P2의 다른 일일 잡처럼 계정 Hermes 설정의 `timezone: Asia/Seoul`로 해석한다.

## 공용 원장과 다음 단계

`automation.auto_write.record(kind, Entry(source_id, path, revision, recorded_at, written_at))`가
`~/.hermes/auto-write/ledger.jsonl`(0600)에 `kind`, 녹음 ID, 노트 경로,
검증된 커밋 SHA, 녹음 시각, `written_at`만 기록한다. 같은 종류·ID·커밋의 재기록은
중복되지 않는다. `summary.json`은 성공한 요약 날짜와 읽은 원장 행 수다.
`enabled(kind)`는 노드 설정만, `release_kinds(home)`는 호스트 스위치만 읽는다.
`summarize(now)`가 종류별 묶음과 워터마크를 공유한다.

Q233(2026-10-18 17:40 KST)의 추가 결정:
> 6개 다 카드 없이 바로 + 하루 요약에 남기기

개인 일정만의 calendar·todo·wiki·Obsidian 노트 요청·project board·
`effect_request drive-save` 연결은 **다음 릴리스 후속**이다. 이번에는 Plaud만 연결하며
다른 종류의 스위치는 거부한다. 공유·외부 초대·메일의 승인 규칙은 바꾸지 않는다.

관련: `automation/auto_write.py`, `auto_write_summary.py`, `auto_write_release.py`,
`automation/plaud_sync/{effects_live.py,watch_step.py,deploy-manifest.txt}`,
`automation/obsidian_write/writer.py`, `tests/unit/test_auto_write*.py`,
`tests/unit/test_plaud_auto_write.py`.
