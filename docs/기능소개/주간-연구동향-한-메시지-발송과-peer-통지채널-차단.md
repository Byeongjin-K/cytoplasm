# 주간 연구 동향 한 메시지 발송과 peer 통지채널 차단

## 무엇을

주간 연구 동향이 `#notifications` 에 **메시지 한 건**으로 간다 — 요약 head(날짜·주제 수·논문
수·주제별 한 줄) 와 전문 `.md` 첨부. 그리고 healthcheck 프로브 `peer_ignored_channels` 가
peer 게이트웨이의 `discord.ignored_channels` 에 `#approvals` 뿐 아니라 **`#notifications` 도**
들어 있어야 PASS 한다(peer 채널 디렉터리에 그 채널이 보일 때).

## 왜

2026-09-21 실측: 24KB 보고가 2000자 청크 7건으로 잘려 채널을 덮었고, 청크는 문장 중간에서
끊겼다(「교하기는 어렵다. 논문 링크: …」). 같은 채널을 수신하던 **peer LLM 게이트웨이가
청크마다(그리고 ops·agent 의 다른 통지마다) 스레드를 열어** 「📬 No home channel is set for
Discord…」 nag 와 논평(「평문 메시지 수신 확인. 🟢」)을 남겼다 — 2026-09-05 에 `#approvals` 에서
닫은 것과 같은 병이다. nag 는 Hermes 가 **새 세션마다 한 번** 내는 안내라 스레드가 새로 생길
때마다 반복됐고, peer 계정에는 home channel 이 없었다.

첨부로 보내는 이유: 보고 파일은 이미 디스크에 있고(`~/notes/research-trends/`, RAG 인제스트
원본) 파사드 `owner_notice.notify_owner(attachments=)` 가 있어(구매 검토 선례) 두 번째 전송
경로 없이 한 메시지가 된다. 청커를 줄 단위로 고치는 길도 있었지만 7건이 5건이 될 뿐
채널이 덮이는 것은 같다.

## 사용 시나리오

- **정기 발송(월~금 09:00 KST, 주 1회)**: 채널에 `**✅ 주간 연구 동향**` 카드 한 건 —
  `📚 주간 연구 동향 — <날짜> KST / 주제 N개 · 논문 M편 — 전문은 첨부 <파일명> / - <주제>: 논문 k편 …`
  과 첨부 `research-trends-<YYYYMMDD>.md`. 실측: 24,443바이트 보고 → head 744자, 청크 1, 첨부 1.
- **출처 실패·빈 검색**: 주제 줄이 `⚠️ 출처 조회 실패` / `검색 결과 없음` 으로 표시되고 전문은
  그대로 첨부된다.
- **파일 없이 부른 레거시 경로**(`_send_dm(report)`): 예전처럼 전문을 본문으로 보낸다.
- **peer 설정 드리프트**: peer config 가 재생성돼 `#notifications` 가 빠지면 healthcheck 가
  `PEER-IGNORED-CHANNELS-MISSING` + `PEER-IGNORED-CHANNELS-RECOVERY` 로 FAIL 한다.
  `#notifications` 가 없는 설치(레거시 DM)는 `#approvals` 만으로 PASS 한다. 두 채널 이름이
  중복이면 fail-closed(`INVALID`).

## 소유자 절차 (노드, 1회)

1. peer `~/.hermes/config.yaml` 최상위 `discord.ignored_channels` 에 `#notifications` id 를
   더한다(`"<approvals id>,<notifications id>"`).
2. peer `~/.hermes/.env` 에 `DISCORD_HOME_CHANNEL=<채널 id>` 를 둔다 — home channel 이 없으면
   Hermes 가 새 세션마다 `/sethome` nag 를 낸다(peer 는 `deliver=discord` cron 이 없어 채널
   선택의 다른 효과는 없다).
3. 게이트웨이 쌍(agent·peer) 재시동(`docs/guide/operations.md` §2).
4. `automation/healthcheck.sh` 의 `peer_ignored_channels` 가 PASS 인지, 다음 통지에 peer
   스레드가 붙지 않는지 확인한다.

## 관련

- `automation/research_trends/research_trends.py::_send_dm` · `research_trends_core.report_summary`
  · `automation/owner_notice.py::send_notice_files` · `automation/peer_gateway_probe.sh`
- 회귀: `tests/unit/test_research_trends_report_attachment.py` ·
  `tests/unit/test_healthcheck_peer_gateway_probe.py`
- [peer 게이트웨이 승인채널 차단 프로브](peer-게이트웨이-승인채널-차단-프로브.md) ·
  [오너 통지 단일 채널](오너-통지-단일-채널.md) ·
  2026-09-05 patch
