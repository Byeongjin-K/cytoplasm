# 노드 밤 배치를 이 PC omo로

노드의 문서·메일 분류·연구 동향 등 배치 모델 호출을 이 PC의 omo 로그인으로
대신 처리한다(Q245). 공용 `codex_llm` API와 16개 호출자는 그대로다.
노드에 omo를 설치하거나 로그인 정보를 복사하지 않는다.
꺼짐 판정과 카드 상수는 순수 `omo_relay_policy`에 두어 승인 게이트의
스테이징에 모델·통지 실행 코드를 끌어들이지 않는다. 꺼짐 경로는 노드 설정도 읽지 않는다.

## 켜기와 되돌리기

릴리스 호스트의 노드 설정(`~/.hermes/node.toml`)에 `omo_relay_target`(SSH 대상)과
`omo_relay_key`(노드 agent 홈의 `.ssh/omo_relay` 절대 경로)를 적는다.
추적 예시는 `configs/node.example.toml`이며 실제 주소·계정은 커밋하지 않는다.
`~/.hermes/release-flags/omo-relay`의 내용이 개행 없는 `on`이면 릴리스 카드에
다음 줄과 설정의 SHA-256 지문이 묶인다.

> 배치 모델 호출: 노드 → 이 PC omo(도구 없음), 파일 삭제 후 재배포로 Hermes 호출 복귀

소유자 릴리스 승인 뒤 배포기가 노드 전용 ed25519 키(없을 때만, 0600)와
`~/.hermes/omo-relay/{mode,node.toml}`을 준비한다. 개인키는 노드 밖으로 보내지 않고
공개키 한 줄만 출력한다. v2 설정 선언은 모드·설정 지문·키 소유권/권한을 재조회한다.
영수증 `omo_relay.route`가 기대 지문이고 설정 artifact가 일치해야 clean이다.
스위치 삭제 후 재배포하면 모드·부분 설정만 제거하며 키는 보존한다.

## 이 PC의 강제명령 연결

리드가 저장소의 `automation/omo_relay/omo-relay`와 `runner.py`를 함께 유지하는
절대 경로를 사용해 이 PC의 `authorized_keys`에 아래 한 줄을 설치한다.
이 카드는 그 파일을 쓰지 않는다. `<node-source-address>`는 노드의 실제 접속 출발 주소,
`<relay-script-absolute-path>`는 이 PC의 실행 가능한 스크립트 경로다.

```text
command="<relay-script-absolute-path>",restrict,from="<node-source-address>" ssh-ed25519 <node-public-key-base64>
```

노드는 처음 접속에서도 호스트 키를 추측하지 않는다. 릴리스 호스트가 곧 중계 대상이므로
배포기가 릴리스 호스트의 `/etc/ssh/ssh_host_ed25519_key.pub`를 대상 호스트 이름과 함께
한 줄로 만들어 승인 지문에 묶고, 노드에는 전용 `~/.hermes/omo-relay/known_hosts`(0600)로만 깐다.
클라이언트는 `StrictHostKeyChecking=yes`·`UserKnownHostsFile=<그 파일>`·`GlobalKnownHostsFile=/dev/null`로
그 한 줄만 믿는다. 승인되지 않은 키나 다른 호스트의 키는 설치 단계에서 거부되고,
릴리스 호스트와 중계 대상이 다르면 접속이 실패한다(조용히 믿지 않음). 노드에서 따로 손댈 단계는 없다.

## 동작과 실패 예

- 밤 메일 분류가 프롬프트를 SSH stdin으로 보낸다. 이 PC는 빈 0700 임시 폴더에서
  `omo -p --mode json --no-tools --no-extensions --no-skills --no-context-files
  --no-session --offline --system-prompt <고정 문구>`로 답한다. 모델 인자는 없다.
  `agent_end.messages` 또는 `message_end.message`의 마지막 assistant에서 텍스트와
  실제 provider/model을 읽어 `{text, provider, model}` JSON 하나만 돌려준다.
- 256 KiB 초과는 rc 2, 동시 두 슬롯이 차면 rc 75, 실행 300초 초과는 rc 124다.
  접속 실패·시간 초과·busy·429는 30초 뒤 한 번만 재시도한 뒤
  `CodexUnavailableError`로 다음 작업에 양보한다. 빈 답은 `CodexError`다.
- 연속 실패 워커 3개(한 워커의 여러 호출·재시도는 한 틱) 또는 30분 지속 실패를
  다음 결과 관측 시 기존 `owner_notice`로 한 번 알리고, 회복도 한 번 알린다.
  전송 실패는 다음 결과에서 재시도한다. doctor의 `primary-model`은 온라인 검사에서
  `이 PC 대신 호출: 닿음/안 닿음`을 표시한다. 빈 stdin 프로브는 모델을 부르지 않는다.

`~/.dori/omo-relay/calls.jsonl`(0600)은 시각·접속 출발지·rc·초·답한 모델·입출력
바이트 수만 남긴다. 프롬프트·답변 본문은 기록하지 않고 임시 폴더는 종료 시 지운다.
로컬 작은 실호출 `say 5`가 반환한 wire 형식은
`tests/unit/fixtures/omo-relay-answer.json`에 있다(본문 `5`, 인증정보 없음).

관련: `automation/omo_relay/`, `automation/omo_relay_release.py`,
`tests/unit/test_omo_relay*.py`, `configs/routing-policy.md`.
