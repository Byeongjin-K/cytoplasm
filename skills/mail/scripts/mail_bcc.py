"""BCC input boundary and address-free owner summaries."""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from email import policy
from email.errors import HeaderParseError
from email.headerregistry import AddressHeader
from pathlib import Path

from triage_gate import GateError


@dataclass(frozen=True, slots=True)
class AddressFile:
    path: str = ""
    column: str = ""


def _mailboxes(value: str, line: int) -> tuple[str, ...]:
    """Parse one address-list value without echoing private input in errors."""
    try:
        if (not value.strip() or "\r" in value or "\n" in value
                or value.strip().startswith(",") or value.strip().endswith(",")):
            raise ValueError
        header = policy.default.header_factory("To", value)
        if not isinstance(header, AddressHeader) or header.defects:
            raise ValueError
        if any(group.display_name is not None for group in header.groups):
            raise ValueError
        addresses = header.addresses
        if not addresses or any(not address.username or not address.domain for address in addresses):
            raise ValueError
        return tuple(address.addr_spec for address in addresses)
    except (ValueError, IndexError, HeaderParseError):
        raise GateError(f"BCC 입력 {line}행: 올바른 메일 주소가 필요합니다", 2) from None


def collect(
    values: tuple[str, ...], source: AddressFile, visible: tuple[str, ...],
) -> tuple[str, ...]:
    """Deduplicate BCC in input order and remove addresses already in To/Cc."""
    if source.column and not source.path:
        raise GateError("--bcc-column 은 --bcc-file 과 함께만 씁니다", 2)
    if not values and not source.path:
        return ()
    entries: list[str] = []
    for line, value in enumerate(values, 1):
        entries.extend(_mailboxes(value, line))
    if source.path:
        try:
            with Path(source.path).expanduser().open(encoding="utf-8-sig", newline="") as stream:
                if source.column:
                    rows = csv.DictReader(stream, strict=True)
                    if not rows.fieldnames or rows.fieldnames.count(source.column) != 1:
                        raise GateError("BCC 입력 1행: 지정한 열을 찾을 수 없습니다", 2)
                    for row in rows:
                        parsed = _mailboxes(row.get(source.column) or "", rows.line_num)
                        if len(parsed) != 1:
                            raise GateError(f"BCC 입력 {rows.line_num}행: 주소 하나만 필요합니다", 2)
                        entries.extend(parsed)
                else:
                    for line, value in enumerate(stream, 1):
                        if not value.strip():
                            continue
                        parsed = _mailboxes(value.rstrip("\r\n"), line)
                        if len(parsed) != 1:
                            raise GateError(f"BCC 입력 {line}행: 주소 하나만 필요합니다", 2)
                        entries.extend(parsed)
        except csv.Error:
            raise GateError(f"BCC 입력 {rows.line_num}행: CSV를 읽을 수 없습니다", 2) from None
        except (OSError, UnicodeError):
            raise GateError("BCC 입력 1행: 파일을 읽을 수 없습니다", 2) from None
    if (values or source.path) and not entries:
        raise GateError("BCC 입력 1행: 주소가 없습니다", 2)
    seen = {
        address.casefold()
        for value in visible if value.strip()
        for address in _mailboxes(value, 1)
    }
    result: list[str] = []
    for address in entries:
        key = address.casefold()
        if key not in seen:
            result.append(address)
            seen.add(key)
    return tuple(result)


def summary(to: str, cc: str, bcc: tuple[str, ...]) -> str:
    """Counts only; never include individual BCC mailboxes on owner surfaces."""
    to_count = len(_mailboxes(to, 1)) if to else 0
    cc_count = len(_mailboxes(cc, 1)) if cc else 0
    domains = Counter(address.rsplit("@", 1)[1].casefold() for address in bcc)
    ranked = domains.most_common(3)
    shown = [f"{domain} {count}명" for domain, count in ranked]
    remaining = sum(domains.values()) - sum(count for _, count in ranked)
    if remaining:
        shown.append(f"기타 {remaining}명")
    return (
        f"총 수신자 {to_count + cc_count + len(bcc)}명 "
        f"(받는 사람 {to_count} · 참조 {cc_count} · 숨은참조 {len(bcc)})\n"
        "숨은참조(BCC)로 보내 받는 사람끼리 주소가 보이지 않습니다 "
        "(To·Cc 주소는 표시됩니다).\n"
        f"숨은참조 도메인: {', '.join(shown)}"
    )
