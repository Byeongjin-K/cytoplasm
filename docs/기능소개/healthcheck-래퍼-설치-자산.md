# healthcheck 래퍼 설치 자산

## 무엇을

설치기는 healthcheck의 SSH 경로를 `ProvisionHealthcheckProbe` 한 작업으로 배치한다.

- `<ops home>/.ssh/autophagy-healthcheck`: ops 소유의 ed25519 전용 키 쌍. 개인키는 0600이다.
- `<operator home>/.ssh/authorized_keys`: 운영자 소유의 0600 파일에 `restrict,command=` 바인딩을 추가한다. `.ssh`는 0700이며 다른 키의 줄과 순서를 보존한다.
- `<operator home>/.local/libexec/autophagy-healthcheck-probe`: 운영자 계정으로 생성한 0755 강제명령 래퍼다.

키의 내용은 설치 계획이나 로그로 내보내지 않는다. 기존 키는 재생성하지 않으며 키 쌍의
한쪽만 남아 있으면 `HEALTHCHECK-KEY-PARTIAL`로 중단한다. 생성기의 완료 표식이 없으면
`HEALTHCHECK-WRAPPER-UNCONFIRMED`로 중단한다.

## 왜

healthcheck는 ops로 실행하지만 SSH 수신 계정은 운영자다. 키만 만들거나 ops 홈에 래퍼를
놓아서는 수신 측 강제명령 경로가 완성되지 않는다. 새 노드에서 마지막 `check healthcheck`가
래퍼 부재 때문에 공통 경로 장애인 `INFRA_FAILURE`로 끝나는 원인을 설치 단계에서 없앤다.
서비스 준비, SSH 호스트 신뢰, 네트워크 장애까지 해결한다는 뜻은 아니다.

설치 순서는 저장소 복제와 설정 파일 배치 다음, 타이머 활성화 전이다. 첫 수렴 전에는
`release_current`가 없으므로 `deploy_checkout`의 생성기를 쓴다. 생성과 지문 검사는 같은
운영자·홈·`/etc/autophagy/node.toml`·배포 체크아웃 환경으로 실행한다. 실제 운영자의 홈은
passwd에서 읽고, 계정이 없는 읽기 전용 계획에서는 관례적인 `/home/<계정>`을 표시한다
(root는 `/root`).

## 사용 시나리오

### 정상 설치와 재실행

노드 설정과 신뢰키를 준비한 뒤 평소 설치 명령을 실행한다.

```bash
python3 -m automation.install --config /root/node.toml --update-trust-key /root/trust.pub
```

`--dry-run`에서도 전용 키와 래퍼 경로가 표시된다. 실제 적용은 운영자 권한으로 생성기를
호출하고 `WRAPPER-INSTALLED` 또는 `WRAPPER-UNCHANGED`를 요구한다. 두 번 적용해도
같은 키의 바인딩은 한 줄이다. 호스트 재검사에서 키·권한·바인딩·래퍼 입력 지문이 모두
맞으면 이 작업은 계획에서 빠진다. 체크아웃이 없거나 지문이 오래됐으면 다시 계획한다.

healthcheck나 서비스 선언을 바꾼 뒤에는 노드에서 운영자 계정으로 재생성한다.

```bash
f=$(mktemp) && sudo -n -u ops -H bash /srv/autophagy-agent-current/automation/healthcheck_probe_wrapper.sh --print <node> > "$f" && bash /srv/autophagy-agent-current/automation/provision-healthcheck-probe.sh --from-file "$f" <node>; rm -f "$f"
```

헬스체크 계정(`ops`)이 생성한다 — 드리프트 프로브는 그 계정의 시야로 판정하고, 비공개 루트의 은퇴 표식은 그 계정만 읽는다. 운영자가 `--install` 로 만들면 은퇴한 행이 남아 지문이 영영 맞지 않는다(2026-10-19 노드 실측).

첫 릴리스가 아직 없다면 위 경로 대신 설정한 배포 체크아웃을 쓴다. 래퍼 본문을 손으로
수정하거나 허용 해시를 별도로 유지하지 않는다.

### 허용되지 않은 명령의 거부

운영자 계정에서 다음 명령으로 거부 경로를 확인할 수 있다.

```bash
SSH_ORIGINAL_COMMAND='echo not-allowlisted' bash "$HOME/.local/libexec/autophagy-healthcheck-probe"
```

결과는 `healthcheck probe denied`, 종료 코드 126이다. 허용된 명령도 해당 서비스가 없으면
자체 실패할 수 있지만, 허용목록 거부와는 구분해야 한다.

## 관련 파일과 검증

- 계획·호스트 검사·적용: `automation/install/{plan,state,apply}.py`
- SSH 자산 처리: `automation/install/healthcheck_probe_asset.py`
- 계획 표시: `automation/install/assets.py`
- 생성기: `automation/healthcheck_probe_wrapper.sh`, `automation/provision-healthcheck-probe.sh`
- 회귀 테스트: `tests/unit/test_install_healthcheck_probe_asset.py`
- systemd 컨테이너 실측: [설치 자산 증거](../qa/INSTALL-TUI/10-wrapper-asset-container.txt)
- 운영 절차: 운영 가이드
