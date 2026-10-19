# 게이트웨이를 켜지 않고 systemd cron 수렴하기

`runner=systemd` 선언의 수렴과 `check`·`check-declared`는 계정 안의
`automation.cron_timer.Node.hermes_jobs`를 함께 쓴다. 전량 배포 프로브도
`check-declared`를 호출하므로 같은 판독·검증을 적용한다.

`hermes cron list --all`이 실패하거나 판독 불가일 때, 계정의
`~/.hermes/gateway-stopped-by-release`가 정확한 바이트 `stopped`이면
`~/.hermes/cron/jobs.json`을 매번 새로 읽는다. 게이트웨이를 잠깐 켤 필요가 없다.
정상 CLI 목록은 계속 우선하며 Hermes 실행 주체의 cron 등록 경로는 변경하지 않는다.

저장소는 러너와 같은 `{"jobs": [...]}` 또는 배열 형태를 받는다.
각 행의 비어 있지 않은 이름·소문자 16진수 ID와 중복되지 않은 ID를 요구하고,
`enabled`는 있으면 불리언, `paused_at`은 있으면 문자열 또는 null이어야 한다.
`enabled=false`는 실행 불가, `enabled=true`는 과거 pause 표식과 무관하게 active다.
옛 레코드에서 `enabled`가 없을 때만 `paused_at`을 pause 근거로 쓴다.
`state=paused`나 `paused=true` 텍스트만으로는 타이머를 켜지 않는다.

활성 잡이 있으면 기존 `hermes cron pause <id>`로 멈춘 뒤 같은 소스를 다시 읽어
확인한다. CLI pause는 Hermes의 잠금·원자 저장을 사용하며 이 수렴기는
jobs.json을 직접 수정하거나 복구하지 않는다. pause가 반영되지 않으면 rc 5로
타이머를 켜지 않고, 같은 이름이 둘 이상이면 기존 rc 7로 거부한다.

정지 표식이 없거나 정확하지 않으면 CLI 목록 판독 실패는 여전히 rc 4다.
정지 표식이 있어도 파일 부재·읽기 실패·잘못된 JSON·불확실한 행은 rc 4로 막는다.
빈 잡은 읽을 수 있는 빈 배열로만 판정한다. 이미 설치된 타이머를 검사할 때
잡 상태를 읽지 못하면 기존 `unknown/unreadable-listing`으로 남는다.
