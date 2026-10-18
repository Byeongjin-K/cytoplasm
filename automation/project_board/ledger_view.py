"""Read-only, live-only view of the account's task ledger."""
from __future__ import annotations

import sqlite3
from html import escape
from typing import TypedDict

from automation.ledger.store import Document, LedgerError, Store
from .dashboard_theme import icon

NAV_CSS = "@media(max-width:640px){nav.ledger-nav{grid-template-columns:repeat(8,minmax(0,1fr))}}"


class LedgerView(TypedDict, total=False):
    boards: list[Document]
    cards: list[Document]
    error: str


_STATUS = {
    "archived": "보관", "blocked": "입력 대기", "done": "완료", "ready": "준비",
    "review": "검토", "running": "진행", "scheduled": "예약", "todo": "할 일", "triage": "분류 대기",
}


def load_ledger() -> LedgerView | None:
    ledger = Store()
    try:
        if not ledger.active():
            return None
        return {"boards": ledger.boards(), "cards": ledger.cards()}
    except (OSError, ValueError, sqlite3.Error, LedgerError):
        return {"error": "할 일 장부를 읽지 못했습니다. 릴리스 장부 점검 결과를 확인해 주세요."}


def render_ledger(view: LedgerView | None) -> str:
    if view is None:
        return ""
    head = ('<div class="head"><div><h2>할 일 장부</h2>'
            '<p class="sub">수리 티켓·회의록 액션·제안서 카드입니다. 이 화면은 읽기 전용입니다.</p></div></div>')
    groups = []
    for board in view.get("boards", []):
        cards = [card for card in view.get("cards", []) if card["project_id"] == board["project_id"]]
        rows = []
        for card in cards:
            status = str(card["status"])
            tone = "st-done" if status == "done" else "st-doing" if status == "running" else "st-held"
            rows.append(
                '<li><div class="todo-main">'
                f'<div><b>{escape(str(card["title"]))}</b> '
                f'<span class="badge {tone}">{escape(_STATUS.get(status, status))}</span></div>'
                f'<div class="small muted">{escape(str(card["id"]))}</div></div></li>'
            )
        body = '<ul class="todos">' + "".join(rows) + "</ul>" if rows else '<p class="note">등록된 카드가 없습니다.</p>'
        groups.append(
            '<details class="fold" open><summary><h3>'
            f'{escape(str(board.get("name") or board.get("slug", "")))} '
            f'<span class="badge d-none">{len(cards)}</span></h3></summary>'
            f'<div class="card">{body}</div></details>'
        )
    body = "".join(groups) or f'<div class="card empty">{icon("todos")}<b>등록된 장부 카드가 없습니다.</b></div>'
    if "error" in view:
        body = f'<div class="card" role="alert">{escape(view["error"])}</div>'
    return f'<section id="ledger" data-tab="ledger">{head}<div class="stack">{body}</div></section>'
