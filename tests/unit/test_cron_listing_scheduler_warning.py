"""2026-10 Hermes 의 게이트웨이 부재 경고도 두 cron 목록 해석기가 같은 방식으로 건너뛰는지 묶는다.

새 Hermes(`hermes_cli/cron.py::_warn_if_gateway_not_running`)는 게이트웨이가 없을 때 옛
`  ⚠  Gateway is not running …` 대신 프로필 이름을 넣은 `  ⚠  Scheduler is not ready for profile …`
2칸 줄을 같은 자리(목록 뒤)에 찍는다. 안내 줄(5칸 이상 들여쓰기) 모양은 같다. 이 줄을 모르는 해석기는
그것을 "다른 2칸 줄"로 보고 목록 전체를 판독 불가로 접는다. 그러면 hermes-gateways=stopped 노드에서
판정기는 모든 cron 행을 unknown 으로, 수렴기는 `CRON-TIMER-BLOCK` 으로 멈춘다.

기존 `test_cron_listing_parser_parity.py` 에 사례를 더하지 않고 새 파일로 둔 이유: tests/AGENTS.md 의
「FS3 정산 레코드가 고정한 테스트 파일에는 케이스를 더하지 않는다」 규칙. 같은 해석기 두 벌과 같은 블록
생성기를 그 파일에서 빌려 쓴다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

from tests.unit.test_cron_listing_parser_parity import _BOX, _GATEWAY_WARNING, _awk, _block, _job, _python

_SCHEDULER_WARNING: Final = (
    "  ⚠  Scheduler is not ready for profile 'default': no gateway serves it and no fresh ticker heartbeat.\n"
    "     If no gateway is running: hermes gateway install\n"
    "                    sudo hermes gateway install --system  # Linux servers\n"
    "     Check status:  hermes cron status\n"
)


def test_scheduler_not_ready_trailer_is_skipped_by_both_parsers(tmp_path: Path) -> None:
    text = _BOX + _block("f00000000003", "w") + _SCHEDULER_WARNING
    want = [_job("f00000000003", "w")]
    assert _python(text) == want
    assert _awk(text, tmp_path) == want


def test_an_unknown_two_space_line_still_makes_the_listing_unreadable(tmp_path: Path) -> None:
    text = _BOX + _block("f00000000004", "w") + "  ⚠  Something else entirely\n"
    assert _python(text) is None
    assert _awk(text, tmp_path) is None


def test_the_older_gateway_trailer_is_still_skipped(tmp_path: Path) -> None:
    text = _BOX + _block("f00000000005", "w") + _GATEWAY_WARNING
    want = [_job("f00000000005", "w")]
    assert _python(text) == want
    assert _awk(text, tmp_path) == want
