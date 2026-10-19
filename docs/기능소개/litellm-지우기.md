# LiteLLM과 일일 비용 보고 지우기

## 무엇을

LiteLLM 스테이징 자산과 `automation/cost-report/` 전체, 09:00 비용 보고 선언·환경변수·전용 시험을 없앤다.
주 모델·폴백과 RAG·recall·embedding은 이 게이트웨이를 사용하지 않는다.

## 왜

2026-10-19 09:08 KST 소유자 결정은 "이거 아예 삭제해 litellm"이다.
일일 비용 보고에도 "이것도 이제 없애라"고 결정했다. 사용하지 않는 중계와
그 전용 비용 장부 보고를 유지하거나 다시 설치하지 않는다.

## 사용 시나리오

릴리스 호스트의 `~/.hermes/release-flags/litellm`에 줄바꿈 없이 정확한 `retired`가 있으면
`litellm:retired`와 지문이 릴리스 승인 해시에 들어간다. 카드에는 다음 한 줄이 추가된다.

> LiteLLM 지우기: 컨테이너 이미지·볼륨·유닛·설정 폴더·agent 키(설정·원장은 백업됨)

소유자 승인 뒤 같은 HEAD의 `deploy_all --apply` 마지막 단계가 다음을 실행한다.
다른 수렴 작업이 없는 경우에도 실행하며, 스위치가 없으면 기존 카드·해시·동작은 그대로다.

- **ops**: `~/.local/state/litellm-retired/20261019/`의 `SHA256SUMS`에 아래 여섯 파일이
  빠짐없이 있고 `sha256sum -c`가 통과해야 한다. 서비스 active/enabled, 지정 이미지에서
  실행 중인 컨테이너, 4000 포트 사용 중이면 지우지 않는다.
  `litellm-gateway.service` 파일과 `~/litellm-gateway`(비밀 포함, 폴더 자체는 미백업),
  정확한 두 볼륨 `autophagy-litellm_litellm_postgres_data`·`autophagy_litellm_postgres_data`를 제거한다.
  지정 이미지 `docker.litellm.ai/berriai/litellm:main-stable`·`postgres:16-alpine`·`python:3.13-alpine`은
  정지 컨테이너라도 사용 중이면 남긴다. Docker는 기존 `sg docker -c` 경로다.
- **agent**: `~/.env.secrets`의 `LITELLM_*` 정의 줄만 제거한다.
  `~/.hermes/config.yaml`의 `providers.litellm`과 `custom_providers`의 `name: litellm` 항목만
  지운다. 두 블록은 `base_url`·`key_env` 두 필드만 허용하며, 예상 밖 구조나 다른 참조가
  있으면 설정을 바꾸기 전에 거부한다. 나머지 바이트·권한은 보존하고 원자 교체 후 재조회한다.
  유일한 항목이었다면 부모는 `providers: {}`·`custom_providers: []`로 남겨 `null`이 되지 않게 한다.
  `send_cost_report.py`와 비활성 `autophagy-cron-daily-cost-report.{timer,service}`도 제거한다.
- 유닛 파일 제거 뒤 해당 계정에서 `daemon-reload`한다. root·계정 삭제·새 권한은 없다.
  실패는 non-zero이며 완료 표식은 마지막에만 쓴다. 계정별
  `~/.local/state/litellm-retired/completed`가 있으면 다음 릴리스에서도 무동작이다.

백업 없음·해시 불일치·활성 유닛·열린 포트·다른 YAML 구조는 성공으로 넘기지 않는다.
이미 끝난 정지·백업은 다시 수행하지 않으며, 이 변경 자체는 노드에 접근하지 않았다.

## 원장 복구

백업 여섯 파일은 `litellm-db.sql.gz`, `volume-autophagy_litellm_postgres_data.tar.gz`,
`config.yaml`, `docker-compose.yml`, `alert_dispatcher.py`, `custom_callbacks.py`다.
이 백업은 삭제하지 않는다. 원장 조회가 필요하면 소유자가 별도의 격리 PostgreSQL 16
데이터베이스를 준비하고, 백업 폴더에서 `sha256sum -c SHA256SUMS`를 먼저 확인한 뒤
`gzip -dc litellm-db.sql.gz | psql -v ON_ERROR_STOP=1 <복구용_DB_접속정보>`로 복원한다.
덤프 안의 원래 역할·DB 이름에 맞춰 복구용 역할과 빈 DB를 준비하며 운영 DB에는 덮어쓰지 않는다.
복구는 원장을 읽기 위한 작업이지 게이트웨이·키·09:00 보고를 다시 켜는 절차가 아니다.
볼륨 tar는 물리 원본 보존용이며 SQL 덤프가 조회 복원의 우선 경로다.

## 관련

`automation/gateway_retirement.py`·`gateway_retirement_policy.py`,
`release.sh`·`release_approval.py`·`release_spec_message.py`·`deploy_all.sh`,
`tests/unit/test_gateway_retirement*.py`·`test_retired_gateway_conformance.py`.
제거 권한은 기존 릴리스 소유자 승인 게이트 하나를 재사용한다.
