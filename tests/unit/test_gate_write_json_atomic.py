"""승인 레코드 쓰기는 원자적이어야 한다 — 독자가 찢어진 파일을 보면 안 된다.

2026-08-01 실측: `test_two_concurrent_producers_post_exactly_once`가 CI(코어가 적은
러너)에서 실패했다. 원인은 테스트의 타이밍 가정이 아니라 **프로덕션 경합**이었다. 패자
프로세스가 `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`으로 죽었는데,
`char 0`은 파일이 **비어 있었다**는 뜻이다 — truncate 후 write 하는 비원자적 쓰기의 전형.

`write_json`은 `path.write_text(...)`였다. 그 창(window)에서 독자는 빈 파일이나 절반만
쓰인 파일을 본다. 그리고 `triage_gate.set_approval_binding`·`set_message_id`는
`json.loads(path.read_text())`를 **예외 처리 없이** 하므로 그 예외가 그대로 밖으로 나간다.

프로덕션 영향: 승인 producer 와 confirm 워처가 같은 레코드를 만진다. `_pending_drafts`
안의 읽기만 fail-closed 로 감싸여 있고, 바인딩 갱신 경로는 감싸여 있지 않아 tick 을 죽인다.

**왜 경합을 재현하지 않고 inode 를 보는가**: 경합 재현은 느리고(실측 6분) 본질적으로
비결정적이라, 스위트에 넣으면 오늘 고친 그 종류의 flake 를 새로 들이는 셈이 된다.
원자적 교체는 대상 이름을 **새 inode** 로 갈아끼우고 truncate 쓰기는 같은 inode 를
유지한다 — 찢어진 읽기가 불가능한 이유가 바로 그 성질이므로, 그것을 직접 고정한다.

같은 구현이 mail·calendar 두 곳에 복제돼 있어 둘 다 고정한다(budget 사본은 2026-10-22 스킬과 함께 은퇴).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[2]

#: (모듈 디렉터리, 임포트 이름) — 같은 결함의 세 사본
_GATES = (
    ("skills/mail/scripts", "triage_mode"),
    ("skills/calendar/scripts", "calendar_gate"),
)


def _load(module_dir: str, module_name: str) -> ModuleType:
    sys.path.insert(0, str(_REPO / module_dir))
    try:
        return __import__(module_name)
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize(("module_dir", "module_name"), _GATES)
def test_rewriting_a_record_swaps_it_in_rather_than_truncating_it(
    module_dir: str, module_name: str, tmp_path: Path
) -> None:
    # Given: 이미 존재하는 승인 레코드
    record = tmp_path / "drafts" / "d-1.json"
    module = _load(module_dir, module_name)
    module.write_json(record, {"id": 1, "body": "가" * 2000})
    first_inode = record.stat().st_ino

    # When: 같은 레코드를 갱신한다 (producer 가 바인딩을 붙일 때 하는 일)
    module.write_json(record, {"id": 2, "body": "나" * 2000})

    # Then: 이름이 새 파일로 갈아끼워졌어야 한다. 같은 inode 라면 제자리에서 잘렸다는
    # 뜻이고, 그 순간 읽는 독자는 빈 파일을 본다.
    assert record.stat().st_ino != first_inode, "제자리 truncate — 찢어진 읽기가 가능하다"
    assert json.loads(record.read_text(encoding="utf-8"))["id"] == 2


@pytest.mark.parametrize(("module_dir", "module_name"), _GATES)
def test_the_record_keeps_owner_only_permissions(
    module_dir: str, module_name: str, tmp_path: Path
) -> None:
    """원자적 교체로 바꾸면서 0600 을 잃기 쉽다 — 승인 레코드는 소유자 전용이어야 한다."""
    record = tmp_path / "drafts" / "d-1.json"
    module = _load(module_dir, module_name)
    module.write_json(record, {"id": 1})
    assert record.stat().st_mode & 0o777 == 0o600
    module.write_json(record, {"id": 2})
    assert record.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(("module_dir", "module_name"), _GATES)
def test_writing_leaves_no_temporary_file_behind(
    module_dir: str, module_name: str, tmp_path: Path
) -> None:
    """임시 파일이 남으면 `*.json` 을 훑는 _pending_drafts 가 그것을 레코드로 읽는다."""
    record = tmp_path / "drafts" / "d-1.json"
    module = _load(module_dir, module_name)
    for index in range(5):
        module.write_json(record, {"id": index})
    assert sorted(path.name for path in record.parent.iterdir()) == ["d-1.json"]


@pytest.mark.parametrize(("module_dir", "module_name"), _GATES)
def test_the_directory_is_created_owner_only(
    module_dir: str, module_name: str, tmp_path: Path
) -> None:
    record = tmp_path / "drafts" / "d-1.json"
    module = _load(module_dir, module_name)
    module.write_json(record, {"id": 1})
    assert record.parent.stat().st_mode & 0o777 == 0o700
