# Project board: existing component contract

This is the existing server-rendered dashboard, not a new visual direction.
The ledger reuses `dashboard_theme.py` without adding styles or dependencies.

## 1. Surface and readers
The owner's authenticated, loopback dashboard. Private ledger titles never enter
the exported dashboard attachment.

## 2. Tokens
`dashboard_theme._TOKENS` owns light/dark colors, `--s1` through `--s10` spacing,
`--r-sm/md/lg` radii and `--t-xs` through `--t-2xl` typography.

## 3. Layout
Reuse the existing main scroll owner, responsive navigation and `stack`.
Boards are native `details` groups; cards form readable single-column lists.
With the live ledger tab, the existing mobile navigation uses eight equal columns
instead of seven, so the extra tab does not create a second fixed row.

## 4. Hierarchy
A section title, board title and count, then card title, status and reference.
Status is Korean text as well as an existing neutral/status badge.

## 5. Primitives and states
Reuse `head`, `fold`, `card`, `todos`, `todo-main`, `badge` and inline Lucide icons.
Ledger entries are selectable text without checkboxes or mutation buttons.
Missing/off: absent. Empty: an explanatory card. Unreadable: an inline error.

## 6. Interaction and motion
Existing tab navigation, fold persistence and refresh remain unchanged.
No new animation, network request or interaction mechanism.

## 7. Accessibility
Native headings, lists and details/summary; escaped text; no status by color alone.
Existing focus rings and responsive wrapping apply to the new section.

## 8. Verification and accepted debt
Check the live section at desktop and phone widths, light and dark, with long
titles, empty boards and an error. Existing theme design is preserved, not audited
or redesigned outside the new section.
