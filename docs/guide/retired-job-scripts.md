# 폐기한 예약 작업의 래퍼 정리

메모리 예약 작업 둘과 알림 폴러를 없앤 뒤에도 계정 홈 `.hermes/scripts/`에
`memory_curator_watch.py`, `memory_relocate_watch.py`, `poll_reminders.py`가 남을 수 있다.
`automation/retired_jobs/deploy-manifest.txt`의 `v2:file;policy=retired` 행이 이 파일들을
배포 인벤토리에 남겨 `UNDECLARED` 판정을 막는다. 이 정책 자체는 삭제하지 않는다.

실제 삭제는 기존 필수 `retired-job-v1` 선언이 수행한다. 유닛이 이미 없어도 래퍼가
남아 있으면 `stale`로 관측하므로 배포기가 정리 경로를 호출한다. 같은 릴리스 승인 뒤
해당 타이머와 서비스를 멈추고 정지를 확인한 다음 유닛과 해당 래퍼만 지운다.
래퍼 경로나 부모가 심링크·마운트이면 삭제 전에 거부한다.
다른 스크립트와 메모리 원본·백업은 보존하며 새 워처나 직접 홈 쓰기 배포기는 없다.

중앙표는 `python3 -m automation.watcher_manifest emit`으로만 재생성한다.
v2 선언은 중앙 legacy 표에 투영되지 않으므로 이 변경으로 중앙표 바이트는 바뀌지 않는다.
검증은 `tests/unit/test_retired_job_scripts.py`와 기존 `test_retired_jobs.py`가 담당한다.
