# Project board: existing component contract

This is the existing server-rendered dashboard, not a new visual direction.
The seventh bottom tab (도리 지시) and the schedule rows' status field reuse `dashboard_theme.py`
and the board's one dropdown (`select_find.py`) without adding styles beyond layout or any dependency.

## 1. Surface and readers
The owner's authenticated, loopback dashboard. The owner's orders never enter
the exported dashboard attachment, so the 도리 지시 tab exists only on the live page.

## 2. Tokens
`dashboard_theme._TOKENS` owns light/dark colors, `--s1` through `--s10` spacing,
`--r-sm/md/lg` radii and `--t-xs` through `--t-2xl` typography.

## 3. Layout
Reuse the existing main scroll owner, responsive navigation and `stack`.
On the live page the mobile navigation uses seven equal columns (요약 · 프로젝트 · 할 일 · 일정 · 근거 · 세션 ·
도리 지시) instead of six, so the extra tab does not create a second fixed row. The ledger tab that held this slot
was removed (owner Q360, 2026-10-23); the exported file keeps six.

## 4. Hierarchy
A section title, group title and count, then row title, status and reference.
Status is Korean text as well as an existing neutral/status badge.

## 5. Primitives and states
Reuse `head`, `fold`, `card`, `todos`, `todo-main`, `badge` and inline Lucide icons.
A fold header folds wherever it is tapped; a link out of a group (a todo group's project) is a button at the
end of the header, never the title. A schedule row's status is the board's one dropdown: a short list that opens
without the keyboard and saves through the same `POST /todo/<id>` as the todo screen.
Missing/off: absent. Empty: an explanatory card. Failure: the row goes back and says why.

## 6. Interaction and motion
Each screen keeps where it was left: a bottom tab returns to its place, the tab already open goes to its top,
a reload or a return from a source page lays the same place again (`sessionStorage` `pb-seen`).
No new animation or network request.

## 7. Accessibility
Native headings, lists and details/summary; escaped text; no status by color alone.
Existing focus rings and responsive wrapping apply to the new tab and field.

## 8. Verification and accepted debt
Check the live page at desktop and phone widths, light and dark, with long titles,
an empty orders list and a failed save. Existing theme design is preserved, not redesigned.
